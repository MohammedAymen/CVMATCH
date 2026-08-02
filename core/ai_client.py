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




class LLMProvider(str, Enum):
    CUSTOM = "custom"   
    GROQ   = "groq"
    GEMINI = "gemini"
    QWEN   = "qwen"




@dataclass
class ProviderState:
    
    name: LLMProvider
    disabled_until: float = 0.0         
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
      
        self.mark_failure(backoff_seconds=3600.0)
        logger.warning(f"🚫 {self.name} quota exhausted — disabled for 1 hour")



class AIClient:
    
    PROVIDER_ORDER = [LLMProvider.GROQ, LLMProvider.GEMINI, LLMProvider.QWEN]

    
    GROQ_MODELS = [
        "llama-3.3-70b-versatile",   
        "openai/gpt-oss-120b",      
    ]

    
    GEMINI_MODELS = [
        "gemini-3.5-flash",      
        "gemini-2.5-flash-lite", 
        "gemini-2.5-flash",      
        "gemini-2.0-flash",      
    ]

   
    QWEN_MODEL = "qwen3:4b-q4_K_M"

    def __init__(
        self,
        groq_api_key: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        ollama_timeout: int = 600,           
        max_retries_per_provider: int = 2,
    ):
        self.groq_api_key   = groq_api_key   or os.getenv("GROQ_API_KEY", "")
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        self.ollama_url     = ollama_url.rstrip("/")
        self.ollama_timeout = ollama_timeout
        self.max_retries    = max_retries_per_provider

       
        self._states: dict[LLMProvider, ProviderState] = {
            p: ProviderState(name=p) for p in LLMProvider
        }

        
        self.QWEN_MODEL = self._detect_qwen_model()

        self._log_startup()

   
    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.2,
        max_tokens: int = 1500,
        
        custom_api_key: Optional[str] = None,
        custom_base_url: Optional[str] = None,
        custom_model: Optional[str] = None,
    ) -> "LLMResponse":
       
        if custom_api_key and custom_base_url:
            try:
                logger.info("🔑 Trying user-supplied custom provider first...")
                text = self._call_custom(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    api_key=custom_api_key,
                    base_url=custom_base_url,
                    model=custom_model or "gpt-4o-mini",
                )
                if text:
                    logger.info(f"✅ [custom] responded ({len(text)} chars)")
                    return LLMResponse(text=text, provider_used=LLMProvider.CUSTOM)
            except Exception as e:
                
                logger.warning(f"⚠️ Custom provider failed, falling back to default chain: {e}")

        last_error = None
        any_attempted = False
        skipped_cooldown = []

        for provider in self.PROVIDER_ORDER:
            state = self._states[provider]

           
            if not self._is_configured(provider):
                logger.debug(f"⏭️ Skipping {provider} (not configured)")
                continue

           
            if not state.is_available():
                secs_left = max(0, state.disabled_until - time.time())
                logger.debug(f"⏭️ Skipping {provider} (cooldown {secs_left:.0f}s remaining)")
                skipped_cooldown.append((provider, secs_left))
                continue

            any_attempted = True
            logger.info(f"🔄 Trying {provider}...")

            
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
                    
                    state.mark_quota_exhausted()
                    break

                except ProviderNotConfiguredError:
                   
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

        
        configured = [p for p in self.PROVIDER_ORDER if self._is_configured(p)]
        if not configured:
            raise AllProvidersFailedError(
                "No providers configured! Set GROQ_API_KEY, GEMINI_API_KEY, "
                "or install a Qwen model via Ollama."
            )
        if not any_attempted:
            cooldown_str = ", ".join(f"{p} ({s:.0f}s left)" for p, s in skipped_cooldown)
            raise AllProvidersFailedError(
                f"All configured providers are on cooldown right now: {cooldown_str}. "
                "Wait for the cooldown to expire, or configure another provider."
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


    def _is_configured(self, provider: LLMProvider) -> bool:
       
        if provider == LLMProvider.GROQ:
            return bool(self.groq_api_key)
        elif provider == LLMProvider.GEMINI:
            return bool(self.gemini_api_key)
        elif provider == LLMProvider.QWEN:
            return bool(self.QWEN_MODEL)
        return False

    def _detect_qwen_model(self) -> Optional[str]:
       
        forced_model = os.getenv("OLLAMA_MODEL", "").strip()
        if forced_model:
            logger.info(f"🦙 Qwen model forced via OLLAMA_MODEL env: {forced_model}")
            return forced_model

        
        preferred = [
            "qwen3:4b-q4_K_M",
            "qwen3:4b",
            "qwen2.5:1.5b",
            "qwen2.5:3b",
            "qwen2.5",
            "qwen2.5:7b",
            "qwen2:7b",
            "qwen2",
            "qwen2.5:14b",
        ]
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            resp.raise_for_status()
            available = [m["name"] for m in resp.json().get("models", [])]

            
            for model in preferred:
                if model in available:
                    logger.info(f"🦙 Qwen model auto-detected: {model}")
                    return model

            
            qwen_models = [m for m in available if "qwen" in m.lower()]
            if qwen_models:
                chosen = qwen_models[0]
                logger.info(f"🦙 Qwen model found: {chosen}")
                return chosen

            
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

    

    def _call_groq(self, prompt: str, system_prompt: str, temperature: float, max_tokens: int) -> str:
        if not self.groq_api_key:
            raise ProviderNotConfiguredError("GROQ_API_KEY not set")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        
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

                
                if resp.status_code == 429:
                    error_body = resp.json().get("error", {})
                    msg = error_body.get("message", "rate limit")
                    logger.warning(f"Groq 429: {msg}")

                    
                    wait_match = _re.search(r"try again in ([\d.]+)s", msg)
                    if wait_match:
                        wait_secs = float(wait_match.group(1)) + 1.0
                        
                        if wait_secs < 30:
                            logger.info(f"⏳ Groq TPM limit — waiting {wait_secs:.1f}s then retrying...")
                            
                            _time.sleep(wait_secs)
                           
                            continue
                    
                    raise QuotaExhaustedError("Groq rate limit / quota exhausted")

                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()

            except QuotaExhaustedError:
                raise  
            except Exception as e:
                last_err = e
                logger.debug(f"Groq model {model} failed: {e}")
                continue

        raise last_err or RuntimeError("All Groq models failed")

   

    def _call_custom(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        base_url: str,
        model: str,
    ) -> str:
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        url = base_url.rstrip("/") + "/chat/completions"
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
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

        if resp.status_code == 401:
            raise ProviderNotConfiguredError("Invalid custom API key")
        if resp.status_code == 429:
            raise QuotaExhaustedError("Custom provider rate limit / quota exhausted")

        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    

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
                generation_config = {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                    "topP": 0.9,
                }
            
                if any(tag in model for tag in ("2.5", "3.5")):
                    generation_config["thinkingConfig"] = {"thinkingBudget": 0}

                payload = {
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": generation_config,
                }
                resp = requests.post(url, json=payload, timeout=60)

                if resp.status_code == 429:
                    raise QuotaExhaustedError(f"Gemini quota exhausted (429)")

                
                if resp.status_code == 404:
                    logger.debug(f"Gemini model {model} not found (404), trying next...")
                    continue

                
                resp.raise_for_status()
                data = resp.json()

                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError("Gemini returned no candidates")

                parts = candidates[0].get("content", {}).get("parts", [])

                
                text = "".join(p.get("text", "") for p in parts).strip()

                
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

   

    def _call_qwen(self, prompt: str, system_prompt: str, temperature: float, max_tokens: int) -> str:
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.QWEN_MODEL,
            "messages": messages,
            "stream": False,
     
            "think": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.85,
                "top_k": 40,
                "repeat_penalty": 1.1,
                "num_predict": max_tokens,
                "num_ctx": 8192,         
                "num_thread": 4,         
                "seed": 42,             
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
            content = data.get("message", {}).get("content", "").strip()
        
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            return content

        except requests.exceptions.ConnectionError:
            raise RuntimeError("Ollama not running — start with: ollama serve")
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Ollama timeout after {self.ollama_timeout}s")

   

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



@dataclass
class LLMResponse:
    text: str
    provider_used: LLMProvider

    def __bool__(self):
        return bool(self.text)




class QuotaExhaustedError(Exception):
    """الـ quota خلصت لهذا الـ provider — روح للتاني."""

class ProviderNotConfiguredError(Exception):
    """الـ API key مش موجود."""

class AllProvidersFailedError(Exception):
    """كل الـ providers فشلوا."""