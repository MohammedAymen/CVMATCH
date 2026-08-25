# استخدم نسخة بايثون مناسبة (slim = أخف بمئات الـ MB من الأساسية)
FROM python:3.9-slim

# System dependencies اللي Playwright/Chromium محتاجها + build tools لـ chromadb/sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*
 
# ضبط مكان الكود داخل الـ Container
WORKDIR /code

# نسخ ملف المكتبات وتثبيتها الأول (Docker layer caching - أسرع في أي rebuild)
COPY ./requirements.txt /code/requirements.txt

# نثبّت نسخة CPU-only من torch الأول (أصغر بكتير من نسخة CUDA اللي pip بيسحبها افتراضي - ~250MB بدل 2GB+)
# مش محتاجين GPU أصلاً على أي free tier، فده بيوفر مساحة ضخمة من غير أي خسارة في الوظيفة
RUN pip install --no-cache-dir torch --extra-index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
 
# تثبيت متصفح Chromium + الـ system libraries اللي محتاجها
# (ده اللي ناقص في نسختك - من غيره الـ scraper هيفشل فورًا جوه الـ container)
RUN playwright install --with-deps chromium \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
 
# نسخ باقي ملفات المشروع (بعد ما نستثني الحاجات الحساسة عبر .dockerignore)
COPY . .
 
# HF Spaces بيشغل الـ container بـ user مش root - نديله صلاحية على مجلده
RUN mkdir -p /code/data /code/logs && chmod -R 777 /code/data /code/logs
 
# Hugging Face Spaces بيستخدم بورت 7860 افتراضياً
EXPOSE 7860
 
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]