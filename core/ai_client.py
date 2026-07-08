
import os
import json
import time
import re
import re as _re
import requests
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field
import time as _time
from core.logger import logger


# ─────────────────────────────────────────────
# Provider Enum
# ─────────────────────────────────────────────

class LLMProvider(str, Enum):
    GROQ   = "groq"
    GEMINI = "gemini"
    QWEN   = "qwen"


# ─────────────────────────────────────────────
# Provider State (quota tracking)
# ─────────────────────────────────────────────

@dataclass
class ProviderState:
    """تتبع حالة كل provider (quota / errors)."""
    name: LLMProvider
    disabled_until: float = 0.0          # timestamp — متاح تاني بعد الوقت ده
    consecutive_failures: int = 0
    total_calls: int = 0
    total_failures: int = 0

    def is_available(self) -> bool:
        return time.time() >= self.disabled_until

    def mark_success(self):
        self.consecutive_failures = 0
        self.total_calls += 1

    def mark_failure(self, backoff_seconds: float = 60.0):
        self.consecutive_failures += 1
        self.total_calls += 1
        self.total_failures += 1
        self.disabled_until = time.time() + backoff_seconds
        logger.warning(
            f"⛔ {self.name} disabled for {backoff_seconds:.0f}s "
            f"(failures: {self.consecutive_failures})"
        )

    def mark_quota_exhausted(self):
        """لما الـ quota تخلص، نعطله لمدة أطول (ساعة مثلاً)."""
        self.mark_failure(backoff_seconds=3600.0)
        logger.warning(f"🚫 {self.name} quota exhausted — disabled for 1 hour")


# ─────────────────────────────────────────────
# Main AIClient
# ─────────────────────────────────────────────

class AIClient:
    """
    Unified LLM client with automatic provider fallback.

    الاستخدام:
        client = AIClient()
        response = client.complete(prompt="...")
        print(response.text)
        print(response.provider_used)
    """

    # الترتيب الافتراضي للـ providers
    PROVIDER_ORDER = [LLMProvider.GROQ, LLMProvider.GEMINI, LLMProvider.QWEN]

    # ─── Groq models (بالترتيب التفضيلي) ───
    GROQ_MODELS = [
        "llama-3.3-70b-versatile",      # الأفضل للـ reasoning
        "llama-3.1-70b-versatile",      # fallback
        "mixtral-8x7b-32768",           # fallback سريع
    ]

    # ─── Gemini models (بالترتيب — بناءً على اختبار فعلي) ───
    GEMINI_MODELS = [
        "gemini-3.5-flash",      # ✅ أفضل أداء + JSON صح
        "gemini-2.5-flash-lite", # ✅ fallback سريع مع mime
        "gemini-2.5-flash",      # fallback
        "gemini-2.0-flash",      # fallback قديم
    ]

    # ─── Qwen via Ollama ───
    # قم بتغيير حسب اللي عندك: qwen2.5:14b أو qwen2.5:7b
    QWEN_MODEL = "qwen2.5:14b"

    def __init__(
        self,
        groq_api_key: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        ollama_timeout: int = 600,           # Qwen local → وقت أطول
        max_retries_per_provider: int = 2,
    ):
        self.groq_api_key   = groq_api_key   or os.getenv("GROQ_API_KEY", "")
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        self.ollama_url     = ollama_url.rstrip("/")
        self.ollama_timeout = ollama_timeout
        self.max_retries    = max_retries_per_provider

        # State لكل provider
        self._states: dict[LLMProvider, ProviderState] = {
            p: ProviderState(name=p) for p in LLMProvider
        }

        # اكتشاف الـ Qwen model المتاح على الجهاز تلقائياً
        self.QWEN_MODEL = self._detect_qwen_model()

        self._log_startup()

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.2,
        max_tokens: int = 1500,
    ) -> "LLMResponse":
        """
        بيبعت الـ prompt للـ providers بالترتيب ولما واحد يفشل يروح للتاني.
        بيرجع LLMResponse فيه الـ text والـ provider اللي اشتغل.
        """
        last_error = None

        for provider in self.PROVIDER_ORDER:
            state = self._states[provider]

            # ── Skip: مش configured (API key فاضي أو model مش موجود) ──
            if not self._is_configured(provider):
                logger.debug(f"⏭️ Skipping {provider} (not configured)")
                continue

            # ── Skip: في cooldown بسبب errors سابقة ──
            if not state.is_available():
                secs_left = max(0, state.disabled_until - time.time())
                logger.debug(f"⏭️ Skipping {provider} (cooldown {secs_left:.0f}s remaining)")
                continue

            logger.info(f"🔄 Trying {provider}...")

            # ── Retry loop لهذا الـ provider ──
            for attempt in range(self.max_retries + 1):
                try:
                    text = self._call_provider(
                        provider=provider,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    if text:
                        state.mark_success()
                        logger.info(f"✅ [{provider}] responded ({len(text)} chars)")
                        return LLMResponse(text=text, provider_used=provider)

                    raise ValueError("Empty response from provider")

                except QuotaExhaustedError:
                    # quota خلصت → عطله ساعة وروح للتاني فوراً
                    state.mark_quota_exhausted()
                    break

                except ProviderNotConfiguredError:
                    # مش المفروض نوصل هنا لأن _is_configured بيمنع ده
                    # بس للأمان نـ break بدون retry
                    logger.debug(f"⏭️ {provider} not configured (caught in retry loop)")
                    break

                except Exception as e:
                    last_error = e
                    is_last_attempt = (attempt == self.max_retries)

                    if is_last_attempt:
                        backoff = 30.0 * (2 ** min(state.consecutive_failures, 4))
                        state.mark_failure(backoff_seconds=backoff)
                        logger.error(f"❌ [{provider}] failed after {attempt + 1} attempts: {e}")
                    else:
                        wait = 2.0 * (2 ** attempt)
                        logger.warning(
                            f"⚠️ [{provider}] attempt {attempt + 1}/{self.max_retries + 1} "
                            f"failed, retry in {wait:.1f}s — {e}"
                        )
                        time.sleep(wait)

        # كل الـ providers المتاحة فشلوا أو مش configured
        configured = [p for p in self.PROVIDER_ORDER if self._is_configured(p)]
        if not configured:
            raise AllProvidersFailedError(
                "No providers configured! Set GROQ_API_KEY, GEMINI_API_KEY, "
                "or install a Qwen model via Ollama."
            )
        raise AllProvidersFailedError(
            f"All providers failed. Last error: {last_error}"
        )

    def get_stats(self) -> dict:
        """إحصائيات الاستخدام لكل provider."""
        return {
            p.value: {
                "total_calls": s.total_calls,
                "total_failures": s.total_failures,
                "consecutive_failures": s.consecutive_failures,
                "available": s.is_available(),
            }
            for p, s in self._states.items()
        }

    def reset_provider(self, provider: LLMProvider):
        """إعادة تفعيل provider بشكل يدوي (مثلاً بعد ما تجدد الـ quota)."""
        self._states[provider] = ProviderState(name=provider)
        logger.info(f"🔄 {provider} state reset")

    # ─────────────────────────────────────────
    # Configuration Helpers
    # ─────────────────────────────────────────

    def _is_configured(self, provider: LLMProvider) -> bool:
        """هل الـ provider ده جاهز للاستخدام؟"""
        if provider == LLMProvider.GROQ:
            return bool(self.groq_api_key)
        elif provider == LLMProvider.GEMINI:
            return bool(self.gemini_api_key)
        elif provider == LLMProvider.QWEN:
            return bool(self.QWEN_MODEL)
        return False

    def _detect_qwen_model(self) -> Optional[str]:
        """
        بيبص على الـ models الموجودة في Ollama ويختار أحسن Qwen.
        بيرجع None لو Ollama مش شغال أو مفيش Qwen model.
        """
        preferred = [
            "qwen2.5:14b",
            "qwen2.5:7b",
            "qwen2.5:3b",
            "qwen2.5:1.5b",
            "qwen2.5",
            "qwen2:7b",
            "qwen2",
        ]
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            resp.raise_for_status()
            available = [m["name"] for m in resp.json().get("models", [])]

            # exact match بالترتيب المفضل
            for model in preferred:
                if model in available:
                    logger.info(f"🦙 Qwen model auto-detected: {model}")
                    return model

            # أي model اسمه فيه "qwen"
            qwen_models = [m for m in available if "qwen" in m.lower()]
            if qwen_models:
                chosen = qwen_models[0]
                logger.info(f"🦙 Qwen model found: {chosen}")
                return chosen

            # اعرض اللي موجود فعلاً علشان يعرف يختار
            logger.warning(
                f"⚠️ No Qwen model in Ollama. Available: {available or 'none'}. "
                f"Install: ollama pull qwen2.5:7b"
            )
            return None

        except requests.exceptions.ConnectionError:
            logger.warning("⚠️ Ollama not reachable — Qwen fallback disabled")
            return None
        except Exception as e:
            logger.warning(f"⚠️ Could not check Ollama models: {e}")
            return None

    # ─────────────────────────────────────────
    # Provider Implementations
    # ─────────────────────────────────────────

    def _call_provider(
        self,
        provider: LLMProvider,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if provider == LLMProvider.GROQ:
            return self._call_groq(prompt, system_prompt, temperature, max_tokens)
        elif provider == LLMProvider.GEMINI:
            return self._call_gemini(prompt, system_prompt, temperature, max_tokens)
        elif provider == LLMProvider.QWEN:
            return self._call_qwen(prompt, system_prompt, temperature, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    # ── Groq ──────────────────────────────────

    def _call_groq(self, prompt: str, system_prompt: str, temperature: float, max_tokens: int) -> str:
        if not self.groq_api_key:
            raise ProviderNotConfiguredError("GROQ_API_KEY not set")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # جرب الـ models بالترتيب لو الأول مش موجود
        last_err = None
        for model in self.GROQ_MODELS:
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.groq_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    timeout=60,
                )

                # Quota / Rate limit
                if resp.status_code == 429:
                    error_body = resp.json().get("error", {})
                    msg = error_body.get("message", "rate limit")
                    logger.warning(f"Groq 429: {msg}")

                    # استخرج الوقت الفعلي من الـ error message لو موجود
                    # "Please try again in 6.285s"
                    
                    wait_match = _re.search(r"try again in ([\d.]+)s", msg)
                    if wait_match:
                        wait_secs = float(wait_match.group(1)) + 1.0
                        # لو الانتظار قصير (< 30s) → sleep وحاول تاني
                        if wait_secs < 30:
                            logger.info(f"⏳ Groq TPM limit — waiting {wait_secs:.1f}s then retrying...")
                            
                            _time.sleep(wait_secs)
                            # retry نفس الـ model
                            continue
                    # لو مش rate limit قصير → quota فعلية
                    raise QuotaExhaustedError("Groq rate limit / quota exhausted")

                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()

            except QuotaExhaustedError:
                raise  # ارفع للـ caller مباشرة
            except Exception as e:
                last_err = e
                logger.debug(f"Groq model {model} failed: {e}")
                continue

        raise last_err or RuntimeError("All Groq models failed")

    # ── Gemini ────────────────────────────────

    def _call_gemini(self, prompt: str, system_prompt: str, temperature: float, max_tokens: int) -> str:
        if not self.gemini_api_key:
            raise ProviderNotConfiguredError("GEMINI_API_KEY not set")

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

        last_err = None
        for model in self.GEMINI_MODELS:
            try:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={self.gemini_api_key}"
                )
                payload = {
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_tokens,
                        "topP": 0.9,
                        # مش بنستخدم responseMimeType — بيسبب تقطيع في بعض models
                        # الـ prompt نفسه بيطلب JSON بشكل صريح
                    },
                }
                resp = requests.post(url, json=payload, timeout=60)

                # Quota exhausted فقط — مش كل error
                if resp.status_code == 429:
                    raise QuotaExhaustedError(f"Gemini quota exhausted (429)")

                # model مش موجود → جرب التاني
                if resp.status_code == 404:
                    logger.debug(f"Gemini model {model} not found (404), trying next...")
                    continue

                # باقي الـ errors → exception عادي يعمل retry
                resp.raise_for_status()
                data = resp.json()

                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError("Gemini returned no candidates")

                parts = candidates[0].get("content", {}).get("parts", [])

                # thoughtSignature هو metadata على الـ part مش part منفصل —
                # النص الفعلي موجود في text بغض النظر عن وجود thoughtSignature
                text = "".join(p.get("text", "") for p in parts).strip()

                # إزالة ```json ``` لو موجودة (gemini-2.5-flash-lite بدون mime)
                if text.startswith("```"):
                    text = text.split("```", 2)[-1] if text.count("```") >= 2 else text
                    text = text.lstrip("json").strip()
                    if text.endswith("```"):
                        text = text[:-3].strip()

                if not text:
                    raise ValueError("Gemini returned empty text")

                return text

            except QuotaExhaustedError:
                raise
            except Exception as e:
                last_err = e
                logger.debug(f"Gemini model {model} failed: {e}")
                continue

        raise last_err or RuntimeError("All Gemini models failed")

    # ── Qwen via Ollama ───────────────────────

    def _call_qwen(self, prompt: str, system_prompt: str, temperature: float, max_tokens: int) -> str:
        """
        Qwen 2.5 عبر Ollama مع إعدادات محسّنة للـ reasoning:
        - temperature منخفضة للدقة
        - top_k محدود لتقليل التشتت
        - repeat_penalty لتجنب التكرار
        - num_ctx كبير لاستيعاب الـ context
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.QWEN_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.85,
                "top_k": 40,
                "repeat_penalty": 1.1,
                "num_predict": max_tokens,
                "num_ctx": 8192,         # context window
                "num_thread": 4,         # CPU threads (عدّل حسب جهازك)
                "seed": 42,              # reproducibility
            },
        }

        try:
            resp = requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=self.ollama_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "").strip()

        except requests.exceptions.ConnectionError:
            raise RuntimeError("Ollama not running — start with: ollama serve")
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Ollama timeout after {self.ollama_timeout}s")

    # ─────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────

    def _log_startup(self):
        parts = []

        if self.groq_api_key:
            parts.append("Groq ✅")
        else:
            parts.append("Groq ⚠️  (no key — skipped)")

        if self.gemini_api_key:
            parts.append("Gemini ✅")
        else:
            parts.append("Gemini ⚠️  (no key — skipped)")

        if self.QWEN_MODEL:
            parts.append(f"Qwen ✅ ({self.QWEN_MODEL})")
        else:
            parts.append("Qwen ⚠️  (no model — skipped)")

        active = [
            p for p in self.PROVIDER_ORDER if self._is_configured(p)
        ]
        chain = " → ".join(p.value for p in active) or "NONE"

        logger.info(f"🤖 AIClient: {' | '.join(parts)}")
        logger.info(f"   Active chain: {chain}")


# ─────────────────────────────────────────────
# Response dataclass
# ─────────────────────────────────────────────

@dataclass
class LLMResponse:
    text: str
    provider_used: LLMProvider

    def __bool__(self):
        return bool(self.text)


# ─────────────────────────────────────────────
# Custom Exceptions
# ─────────────────────────────────────────────

class QuotaExhaustedError(Exception):
    """الـ quota خلصت لهذا الـ provider — روح للتاني."""

class ProviderNotConfiguredError(Exception):
    """الـ API key مش موجود."""

class AllProvidersFailedError(Exception):
    """كل الـ providers فشلوا."""