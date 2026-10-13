# TALOS Unified v2.2 — GitHub Ready (Research + Telegram)

هذه حزمة **كود متكاملة للاختبار والترحيل** وليست إقرارًا بأن النظام نُشر على Render أو أن التداول الحقيقي مفعّل.

## ما يشتغل فعلًا
- واجهة Phase 2 المعتمدة على `/lab` بنفس ملف الخوذة والحركة والألوان؛ `web/APPROVED_ORIGINAL.html` مرجع التصميم دون تعديل.
- API وJob Queue وWorker وبيانات CSV وBacktest المرجعي وWalk-forward وMonte Carlo ومتانه المعلمات وReplay متعدد المصادر وPhanes XLSX.
- Telegram Bot v2.1 مدمج **من حيث الكود والتشغيل** مع `/status`, `/strategies`, `/jobs`, `/job`, `/backtest`, `/tgtest`, `/sync`, `/replay`, `/verify`, `/risk`, وملفات Phanes. يستعمل **نفس توكن وإعدادات البوت الموجود** في `C:\TalosEngines\TalosBotV2\bot_config.json`، دون إنشاء بوت آخر.
- تظل الأوامر الحقيقية مقفلة. LEAN/NautilusTrader native عام غير معتمد؛ Stage 3 SMA fixture فقط قابل للتنفيذ الأصلي على جهاز Windows حيث تم تثبيته.

## أ) التشغيل المحلي الموحد — **أوقف عملية البوت القديمة أولًا** كي لا يتنافس برنامجان على getUpdates

استخرج محتويات ZIP مباشرة إلى `C:\TalosEngines\TalosUnified` بحيث يوجد `scripts` و`server` و`web` في الجذر، ثم شغّل **أمرًا واحدًا**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\TalosEngines\TalosUnified\scripts\START_ALL.ps1"
```

يفتح ثلاث نوافذ PowerShell (API وWorker وBot). متصفح: `http://127.0.0.1:8018/lab`.

**تنبيه:** إذا كان TalosFinal يشغل المنفذ 8018 بالفعل، أغلق نافذة API وWorker القديمتين قبل تشغيل النسخة الموحدة؛ لا تُشغل نسختين على المنفذ نفسه. إذا لم تجد نسخة إعدادات البوت القديمة، يولّد `bot_config.json` فارغًا محليًا لتضع التوكن في جهازك.

## ب) النشر على Render الموجود
- لا ترفع ZIP داخل GitHub؛ **افك ضغطه أولًا** وراجع الملفات في فرع `staging` قبل الدمج في `main`.
- Dockerfile هنا يستبدل الـBackend القديم بخادم Research Plane v2.2 ويخدم `/lab` فقط، فلا تفعّله على خدمة v4.7.8 الإنتاجية قبل تصدير الأرشيف والتحقق من أي وظائف قديمة تريد استمرارها.
- يلزم `TALOS_MODE=hosted` (محدد في Dockerfile)، `TALOS_LAB_DATABASE_URL` اتصال PostgreSQL/Neon، `TALOS_LAB_ADMIN_TOKEN`، `TALOS_LAB_WORKER_TOKEN` (مفتاحان مختلفان >=32 حرفًا). **لا تدخل أي مفتاح في GitHub أو ترسله لي**. قاعدة SQLite المحلية غير مناسبة لـRender ذات نظام الملفات المؤقت.
- لا تغير تلقائيًا جداول v4.7.8؛ تستخدم الجداول المعزولة `lab_...`. نقل أرشيف Telegram القديم يحتاج مسار المزامنة للقراءة فقط.
- من جهاز Windows اتصل بالـAPI الجديد عبر HTTPS باستخدام `scripts/START_REMOTE_WORKER.ps1 -ApiUrl https://YOUR-SERVICE.onrender.com` و`START_REMOTE_BOT.ps1 -ApiUrl https://YOUR-SERVICE.onrender.com` في نافذتين. لا ترسل التوكن أو أسرار الإدارة إلي.
- الـFrontend الحالي على Render **لن يتبدل تلقائيًا**؛ يجب أن يشير نشر واجهته إلى الملف المعتمد، أو تُستخدم `/lab` في خدمة Research. لا يجوز وصف نشر GitHub وحده بأنه نشر ناجح.

## حدود مهمة
- لا تكشف أي توكن من `bot_config.json` أو `.local_auth.json` أو متغيرات البيئة على الإنترنت؛ الملفات مُستبعدة عبر `.gitignore`.
- Telegram Bot API لا يكشف حذوفات منشورات القنوات بصورة عامة؛ لا ندعي ذلك. البيانات قبل إضافة البوت لا تصبح أصيلة تلقائيًا.
- `/missed` يعرض نتائج WIN/LOSS من آخر توصيات جرى تقييمها بواسطة `/verify` فقط مع منع تكرار التسجيل؛ ليست فرص Sentiment فائتة تلقائية وليست أرباح تداول حقيقية. إرسال تنبيهات فائتة آليًا ما زال يحتاج مصدر أسعار وتوقيت ونتائج مثبتة.
- مصدر الأخبار/الساتلايت/Insider/Dark Pools وقوالب Native العامة ما زال إضافات بحثية غير معتمدة؛ لا ندعي اكتمالها.
- Web research API لا يصدّر جلسات التداول ولا توجد أوامر live إطلاقًا.

راجع `docs/STATUS_AND_MIGRATION_AR.md`.
