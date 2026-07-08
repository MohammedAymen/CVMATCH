import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, Union

import requests
from requests.structures import CaseInsensitiveDict

from core.config import settings
from core.logger import logger

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    _HAS_TEXT_SPLITTER = True
except ImportError:  # pragma: no cover
    RecursiveCharacterTextSplitter = None  # type: ignore
    _HAS_TEXT_SPLITTER = False
    logger.debug(
        "langchain-text-splitters not installed; oversized chunks will be split "
        "with a simple fallback splitter instead."
    )

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL_API = "https://api.github.com/graphql"

DEFAULT_TIMEOUT = 15
DEFAULT_MAX_RETRIES = 3
DEFAULT_CACHE_TTL = 3600              # ساعة واحدة
DEFAULT_MAX_REPO_PAGES = 3            # حد أقصى 300 repo
DEFAULT_MAX_REPOS_FOR_LANGUAGES = 10  # عدد الـ repos التي تُجلب لغاتها بالتفصيل عبر REST
DEFAULT_MAX_CHUNK_CHARS = 1200        # حد أقصى تقريبي (~300 توكن) قبل تقسيم أي chunk
DEFAULT_CHUNK_OVERLAP = 100


class _SimpleCache:
    """Cache بسيط في الذاكرة مع TTL، آمن بين الـ threads بشكل أساسي."""

    def __init__(self, ttl: int = DEFAULT_CACHE_TTL):
        self._ttl = ttl
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            timestamp, value = entry
            if time.time() - timestamp > self._ttl:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class GitHubFetcher:
    
    _GRAPHQL_QUERY = """
    query($login: String!, $repoCount: Int!, $langCount: Int!) {
      user(login: $login) {
        login
        name
        bio
        location
        company
        websiteUrl
        email
        url
        createdAt
        followers { totalCount }
        following { totalCount }
        publicRepos: repositories(ownerAffiliations: OWNER, privacy: PUBLIC) {
          totalCount
        }
        repositories(
          first: $repoCount
          isFork: false
          ownerAffiliations: OWNER
          orderBy: {field: STARGAZERS, direction: DESC}
        ) {
          nodes {
            name
            nameWithOwner
            description
            url
            stargazerCount
            forkCount
            updatedAt
            primaryLanguage { name }
            repositoryTopics(first: 10) {
              nodes { topic { name } }
            }
            languages(first: $langCount, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node { name }
              }
            }
          }
        }
      }
    }
    """

    def __init__(
        self,
        token: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        use_cache: bool = True,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.token = token or settings.github_token
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()

        if self.token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.v3+json",
            })
            logger.info("GitHub fetcher initialized with token (REST: 5000/hour, GraphQL enabled)")
        else:
            logger.warning(
                "GitHub fetcher initialized without token. "
                "REST rate limit is 60 requests/hour and GraphQL is unavailable."
            )

        self._rate_limit_remaining: Optional[int] = None
        self._rate_limit_reset: Optional[float] = None

        self._cache = _SimpleCache(cache_ttl) if use_cache else None

 
    def __enter__(self) -> "GitHubFetcher":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """يغلق الـ HTTP session."""
        self.session.close()

 
    def _update_rate_limit(self, headers: CaseInsensitiveDict) -> None:
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
        if remaining is None or reset is None:
            return
        try:
            self._rate_limit_remaining = int(remaining)
            self._rate_limit_reset = float(reset)
        except ValueError:
            pass

    def _maybe_wait_for_rate_limit(self) -> None:
    
        if self._rate_limit_remaining is None or self._rate_limit_reset is None:
            return
        if self._rate_limit_remaining >= 5:
            return

        wait_time = max(0.0, self._rate_limit_reset - time.time()) + 1
        if wait_time > 0:
            logger.warning(
                f"Rate limit almost exhausted ({self._rate_limit_remaining} left). "
                f"Waiting {wait_time:.0f}s until reset..."
            )
            time.sleep(wait_time)


    def _request(self, url: str, params: Optional[Dict] = None) -> Optional[Union[Dict, List]]:
        """
        ينفّذ GET request مع معالجة الأخطاء، rate limiting، وعدد محاولات محدود.
        يرجع None في حالة الفشل النهائي بعد كل المحاولات.
        """
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            self._maybe_wait_for_rate_limit()

            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                self._update_rate_limit(resp.headers)
                resp.raise_for_status()
                return resp.json()

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else None

                if status == 401:
                    logger.error("GitHub token invalid or expired.")
                    return None

                if status == 404:
                    logger.error(f"Resource not found: {url}")
                    return None

                if status == 403:
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after is not None:
                        wait_time = float(retry_after)
                    elif self._rate_limit_reset is not None:
                        wait_time = max(0.0, self._rate_limit_reset - time.time()) + 1
                    else:
                        wait_time = 60.0

                    if attempt < self.max_retries:
                        logger.warning(
                            f"403 (rate limit?) on attempt {attempt + 1}/{self.max_retries + 1}. "
                            f"Waiting {wait_time:.0f}s before retry..."
                        )
                        time.sleep(wait_time)
                        last_error = e
                        continue

                    logger.error(f"403 persisted after {self.max_retries} retries: {url}")
                    return None

                logger.error(f"HTTP error {status} for {url}: {e}")
                return None

            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < self.max_retries:
                    wait_time = 2 ** attempt  # 1, 2, 4 ...
                    logger.warning(
                        f"Request failed (attempt {attempt + 1}/{self.max_retries + 1}): {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                logger.error(f"Request failed after {self.max_retries} retries: {e}")
                return None

        if last_error:
            logger.error(f"Giving up on {url} after retries: {last_error}")
        return None


    def _graphql_request(self, query: str, variables: Dict) -> Optional[Dict]:
        """ينفّذ طلب GraphQL. يتطلب token. يرجع None لو فشل أو لا يوجد token."""
        if not self.token:
            return None

        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            self._maybe_wait_for_rate_limit()
            try:
                resp = self.session.post(
                    GITHUB_GRAPHQL_API,
                    json={"query": query, "variables": variables},
                    timeout=self.timeout,
                )
                self._update_rate_limit(resp.headers)
                resp.raise_for_status()
                payload = resp.json()

                if "errors" in payload:
                    logger.error(f"GraphQL errors: {payload['errors']}")
                    return None
                return payload.get("data")

            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < self.max_retries:
                    wait_time = 2 ** attempt
                    logger.warning(f"GraphQL request failed, retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
                    continue

        logger.error(f"GraphQL request failed after retries: {last_error}")
        return None

    # REST 
    def fetch_user_profile(self, username: str) -> Dict[str, Any]:
        cache_key = f"profile:{username}"
        if self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        url = f"{GITHUB_API}/users/{username}"
        data = self._request(url)
        if not data or not isinstance(data, dict):
            return {}

        profile = {
            "login": data.get("login"),
            "name": data.get("name"),
            "bio": data.get("bio"),
            "location": data.get("location"),
            "company": data.get("company"),
            "blog": data.get("blog"),
            "email": data.get("email"),
            "public_repos": data.get("public_repos"),
            "followers": data.get("followers"),
            "following": data.get("following"),
            "html_url": data.get("html_url"),
            "created_at": data.get("created_at"),
        }

        if self._cache:
            self._cache.set(cache_key, profile)
        return profile

    
    def fetch_repos(
        self,
        username: str,
        max_repos: int = 30,
        max_pages: int = DEFAULT_MAX_REPO_PAGES,
    ) -> List[Dict]:
    
        cache_key = f"repos:{username}:{max_repos}:{max_pages}"
        if self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        all_repos: List[Dict] = []
        for page in range(1, max_pages + 1):
            url = f"{GITHUB_API}/users/{username}/repos"
            params = {"per_page": 100, "page": page, "sort": "updated"}
            data = self._request(url, params)

            if not data or not isinstance(data, list):
                break

            all_repos.extend(data)
            if len(data) < 100:
                break

            time.sleep(0.1)

        own_repos = [r for r in all_repos if not r.get("fork", False)]
        top_repos = sorted(
            own_repos, key=lambda r: r.get("stargazers_count", 0), reverse=True
        )[:max_repos]

        logger.info(f"Fetched {len(top_repos)} top repos out of {len(own_repos)} own repos for @{username}")

        if self._cache:
            self._cache.set(cache_key, top_repos)
        return top_repos

    # ------------------------------------------------------------------ #
    # REST: languages (محدودة لتجنب N+1)
    # ------------------------------------------------------------------ #
    def fetch_languages_for_repo(self, repo_full_name: str) -> Dict[str, int]:
        cache_key = f"languages:{repo_full_name}"
        if self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        url = f"{GITHUB_API}/repos/{repo_full_name}/languages"
        data = self._request(url)
        result = data if isinstance(data, dict) else {}

        if self._cache:
            self._cache.set(cache_key, result)
        return result

    def aggregate_languages(
        self,
        repos: List[Dict],
        max_repos_for_languages: int = DEFAULT_MAX_REPOS_FOR_LANGUAGES,
    ) -> Dict[str, int]:
        
        lang_bytes: Dict[str, int] = defaultdict(int)

        for repo in repos:
            primary = repo.get("language")
            if primary:
                lang_bytes[primary] += repo.get("size") or 1

        for repo in repos[:max_repos_for_languages]:
            full_name = repo.get("full_name")
            if not full_name:
                continue
            langs = self.fetch_languages_for_repo(full_name)
            for lang, bytes_count in langs.items():
                lang_bytes[lang] += bytes_count

        return dict(lang_bytes)

    def fetch_profile_via_graphql(
        self, username: str, max_repos: int = 30, langs_per_repo: int = 5
    ) -> Optional[Dict[str, Any]]:
        """
        يجلب البروفايل + الـ repos + اللغات في طلب واحد فقط عبر GraphQL.
        يتطلب وجود token. يرجع None لو لا يوجد token أو فشل الطلب أو المستخدم غير موجود.
        """
        if not self.token:
            return None

        cache_key = f"graphql:{username}:{max_repos}:{langs_per_repo}"
        if self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        variables = {"login": username, "repoCount": max_repos, "langCount": langs_per_repo}
        data = self._graphql_request(self._GRAPHQL_QUERY, variables)

        if not data or not data.get("user"):
            return None

        if self._cache:
            self._cache.set(cache_key, data)
        return data

   
    @staticmethod
    def _graphql_user_to_profile(user: Dict) -> Dict[str, Any]:
        return {
            "login": user.get("login"),
            "name": user.get("name"),
            "bio": user.get("bio"),
            "location": user.get("location"),
            "company": user.get("company"),
            "blog": user.get("websiteUrl"),
            "email": user.get("email"),
            "public_repos": (user.get("publicRepos") or {}).get("totalCount"),
            "followers": (user.get("followers") or {}).get("totalCount"),
            "following": (user.get("following") or {}).get("totalCount"),
            "html_url": user.get("url"),
            "created_at": user.get("createdAt"),
        }

    @staticmethod
    def _graphql_repos_to_rest_format(nodes: List[Dict]) -> List[Dict]:
        repos = []
        for node in nodes:
            topics = [
                t["topic"]["name"]
                for t in (node.get("repositoryTopics") or {}).get("nodes", [])
            ]
            repos.append({
                "name": node.get("name"),
                "full_name": node.get("nameWithOwner"),
                "description": node.get("description"),
                "language": (node.get("primaryLanguage") or {}).get("name"),
                "stargazers_count": node.get("stargazerCount", 0),
                "forks_count": node.get("forkCount", 0),
                "topics": topics,
                "html_url": node.get("url"),
                "updated_at": node.get("updatedAt"),
                "_languages_detail": {
                    edge["node"]["name"]: edge["size"]
                    for edge in (node.get("languages") or {}).get("edges", [])
                },
            })
        return repos

    @staticmethod
    def _aggregate_languages_from_graphql_repos(repos: List[Dict]) -> Dict[str, int]:
        lang_bytes: Dict[str, int] = defaultdict(int)
        for repo in repos:
            for lang, size in repo.get("_languages_detail", {}).items():
                lang_bytes[lang] += size
        return dict(lang_bytes)

   
    def build_profile_text(self, profile: Dict) -> str:
        if not profile:
            return ""

        return (
            f"GitHub Profile: @{profile.get('login', 'unknown')}\n"
            f"Name: {profile.get('name', 'N/A')}\n"
            f"Bio: {profile.get('bio', 'N/A')}\n"
            f"Location: {profile.get('location', 'N/A')}\n"
            f"Company: {profile.get('company', 'N/A')}\n"
            f"Blog: {profile.get('blog', 'N/A')}\n"
            f"Email: {profile.get('email', 'N/A')}\n"
            f"Public Repos: {profile.get('public_repos', 0)}\n"
            f"Followers: {profile.get('followers', 0)}\n"
            f"Following: {profile.get('following', 0)}\n"
            f"URL: {profile.get('html_url', '')}\n"
            f"Account Created: {profile.get('created_at', 'N/A')}"
        )

    def build_repos_text(self, repos: List[Dict]) -> str:
        if not repos:
            return ""

        parts = ["## GitHub Repositories\n"]
        for repo in repos:
            topics = ", ".join(repo.get("topics", [])) or "none"
            repo_text = (
                f"Repository: {repo.get('name')}\n"
                f"Description: {repo.get('description') or 'No description'}\n"
                f"Language: {repo.get('language') or 'N/A'}\n"
                f"Stars: {repo.get('stargazers_count', 0)}\n"
                f"Forks: {repo.get('forks_count', 0)}\n"
                f"Topics: {topics}\n"
                f"URL: {repo.get('html_url', '')}\n"
                f"Last Updated: {repo.get('updated_at', 'N/A')}"
            )
            parts.append(repo_text)

        return "\n\n".join(parts)

    def build_languages_text(self, lang_bytes: Dict[str, int]) -> str:
        if not lang_bytes:
            return ""

        total = sum(lang_bytes.values())
        if total == 0:
            return ""

        sorted_langs = sorted(lang_bytes.items(), key=lambda x: x[1], reverse=True)
        lines = ["## Programming Languages Used\n"]
        for lang, bytes_count in sorted_langs[:15]:
            pct = (bytes_count / total) * 100
            lines.append(f"  - {lang}: {pct:.1f}%")
        return "\n".join(lines)

   
    def _split_if_needed(
        self,
        text: str,
        max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> List[str]:
        """
        يرجع [text] كما هو لو كان أقصر من max_chars (الحالة الشائعة لكل repo).
        لو أطول، يستخدم RecursiveCharacterTextSplitter لو متاح، وإلا
        تقسيم بسيط بالطول مع overlap.
        """
        if len(text) <= max_chars:
            return [text]

        if _HAS_TEXT_SPLITTER:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=max_chars,
                chunk_overlap=overlap,
                separators=["\n\n", "\n", ". ", " ", ""],
            )
            return splitter.split_text(text)

        # Fallback بسيط لو المكتبة غير مثبتة
        pieces = []
        step = max(1, max_chars - overlap)
        for start in range(0, len(text), step):
            pieces.append(text[start:start + max_chars])
            if start + max_chars >= len(text):
                break
        return pieces

    def build_repo_documents(
        self,
        repos: List[Dict],
        username: str,
        max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    ) -> List[Dict[str, Any]]:
       
        documents: List[Dict[str, Any]] = []

        for repo in repos:
            topics = ", ".join(repo.get("topics", [])) or "none"
            text = (
                f"Repository: {repo.get('name')} (by @{username})\n"
                f"Description: {repo.get('description') or 'No description'}\n"
                f"Primary Language: {repo.get('language') or 'N/A'}\n"
                f"Stars: {repo.get('stargazers_count', 0)} | Forks: {repo.get('forks_count', 0)}\n"
                f"Topics: {topics}\n"
                f"URL: {repo.get('html_url', '')}\n"
                f"Last Updated: {repo.get('updated_at', 'N/A')}"
            )

            base_metadata = {
                "type": "repo",
                "username": username,
                "repo_name": repo.get("name"),
                "language": repo.get("language"),
                "stars": repo.get("stargazers_count", 0),
                "url": repo.get("html_url"),
            }

            pieces = self._split_if_needed(text, max_chars)
            for idx, piece in enumerate(pieces):
                metadata = dict(base_metadata)
                if len(pieces) > 1:
                    metadata["chunk_index"] = idx
                documents.append({"text": piece, "metadata": metadata})

        return documents


    def _fetch_profile_repos_languages(
        self,
        username: str,
        max_repos: int = 30,
        fetch_languages: bool = True,
        max_repos_for_languages: int = DEFAULT_MAX_REPOS_FOR_LANGUAGES,
    ) -> Tuple[Dict[str, Any], List[Dict], Dict[str, int]]:
        """
        يجلب الـ profile + الـ repos + اللغات، مع تفضيل GraphQL (طلب واحد فقط)
        لو متوفر token، وإلا REST مع fallback.

        منطق مشترك تستخدمه get_profile_documents و get_profile_chunks.
        """
        profile: Dict[str, Any] = {}
        repos: List[Dict] = []
        lang_bytes: Dict[str, int] = {}

        # المسار الأول: GraphQL (سريع واقتصادي، يتطلب token)
        if self.token:
            graphql_data = self.fetch_profile_via_graphql(username, max_repos)
            if graphql_data and graphql_data.get("user"):
                user = graphql_data["user"]
                profile = self._graphql_user_to_profile(user)
                repos = self._graphql_repos_to_rest_format(
                    (user.get("repositories") or {}).get("nodes", [])
                )
                if fetch_languages:
                    lang_bytes = self._aggregate_languages_from_graphql_repos(repos)

        # المسار الثاني: REST API (fallback لو لا يوجد token أو فشل GraphQL)
        if not profile:
            profile = self.fetch_user_profile(username)
            if not profile:
                return {}, [], {}

            repos = self.fetch_repos(username, max_repos)

            if fetch_languages and repos:
                lang_bytes = self.aggregate_languages(repos, max_repos_for_languages)

        return profile, repos, lang_bytes

    def get_profile_documents(
        self,
        username: str,
        max_repos: int = 30,
        fetch_languages: bool = True,
        max_repos_for_languages: int = DEFAULT_MAX_REPOS_FOR_LANGUAGES,
        max_chars_per_chunk: int = DEFAULT_MAX_CHUNK_CHARS,
    ) -> List[Dict[str, Any]]:
       
        logger.info(f"Fetching GitHub data for @{username}...")

        profile, repos, lang_bytes = self._fetch_profile_repos_languages(
            username, max_repos, fetch_languages, max_repos_for_languages
        )
        if not profile:
            logger.error(f"Could not fetch profile for @{username}")
            return []

        documents: List[Dict[str, Any]] = []

        profile_text = self.build_profile_text(profile)
        if profile_text:
            for piece in self._split_if_needed(profile_text, max_chars_per_chunk):
                documents.append({
                    "text": piece,
                    "metadata": {"type": "profile", "username": username},
                })

        documents.extend(self.build_repo_documents(repos, username, max_chars_per_chunk))

        if lang_bytes:
            langs_text = self.build_languages_text(lang_bytes)
            if langs_text:
                for piece in self._split_if_needed(langs_text, max_chars_per_chunk):
                    documents.append({
                        "text": piece,
                        "metadata": {"type": "languages", "username": username},
                    })

        logger.info(f"Generated {len(documents)} chunks from GitHub profile for @{username}")
        return documents

    def get_profile_chunks(
        self,
        username: str,
        max_repos: int = 30,
        fetch_languages: bool = True,
        max_repos_for_languages: int = DEFAULT_MAX_REPOS_FOR_LANGUAGES,
        max_chars_per_chunk: int = DEFAULT_MAX_CHUNK_CHARS,
    ) -> List[str]:
        
        documents = self.get_profile_documents(
            username, max_repos, fetch_languages, max_repos_for_languages, max_chars_per_chunk
        )
        return [doc["text"] for doc in documents]



def load_github_profile(
    username: str,
    token: Optional[str] = None,
    max_repos: int = 30,
    fetch_languages: bool = True,
    max_repos_for_languages: int = DEFAULT_MAX_REPOS_FOR_LANGUAGES,
) -> List[str]:
    """
    واجهة بسيطة لجلب ملف GitHub كامل وتقطيعه إلى نصوص (chunks) جاهزة للـ embedding.
    كل repo بقى chunk مستقل، بدون metadata.
    """
    with GitHubFetcher(token) as fetcher:
        return fetcher.get_profile_chunks(
            username,
            max_repos=max_repos,
            fetch_languages=fetch_languages,
            max_repos_for_languages=max_repos_for_languages,
        )


def load_github_profile_documents(
    username: str,
    token: Optional[str] = None,
    max_repos: int = 30,
    fetch_languages: bool = True,
    max_repos_for_languages: int = DEFAULT_MAX_REPOS_FOR_LANGUAGES,
) -> List[Dict[str, Any]]:
   
    with GitHubFetcher(token) as fetcher:
        return fetcher.get_profile_documents(
            username,
            max_repos=max_repos,
            fetch_languages=fetch_languages,
            max_repos_for_languages=max_repos_for_languages,
        )