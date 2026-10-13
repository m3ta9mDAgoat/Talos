# Talos Unified v2.3 — Connected Research Web Release

**ما تغير عن v2.2:** يعرض الموقع على Render واجهة Phase 2 مع صفحات Data Library وJobs & Operations وPaper Portfolio وDeployments المتصلة بالـAPI، ويشغّل **عامل Backtest مرجعيًا داخل خدمة Render** بدون تشغيل Windows. يجب إدخال Admin token في نافذة **Connect Backend** بعد فتح الموقع؛ لا تحفظ الواجهة التوكن في localStorage. لا تغيير لشعار الخوذة الأصلي أو ملف الموافقة المرجعي.

**نشر تحديث الموقع الموجود** (الموقع https://talos-unified-v2.onrender.com، وخدمة talos-unified-v2). افتح ZIP في `C:\TalosEngines\TalosDeployV23` ثم في PowerShell نفّذ:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\TalosEngines\TalosDeployV23\scripts\PUBLISH_REPLACE_GITHUB.ps1"
```

اكتب `REPLACE` للتأكيد. يجب أن يظهر Render auto-deploy من commit جديد. لا تعيد إنشاء قاعدة البيانات ولا تغير مفاتيح Render إذا كانت الخدمة v2.2 حالتها Live.

**أول اختبار:** افتح الموقع → Connect backend (Admin token من بيئة Render نفسها، وليس Worker token) → Strategies → اختر Synthetic sample → Queue research job → Jobs & Operations → Refresh jobs. يتوقع ظهور `complete` للمهام المرجعية مع مقياس وملف نتيجة؛ Synthetic sample **ليس بيانات سوق حقيقية**. وللبحث على بياناتك الحقيقية: Data Library → استيراد CSV → Use this dataset → Strategies.

**الحدود المعلنة:** Hosted worker ينفّذ `research` و`telegram_replay` المرجعيين فقط؛ لا يشغل Native LEAN/NautilusTrader على Render ولا يقبل أوامر بورصة حقيقية. بوت Telegram v2.1 يظل عملية منفصلة ولا يصبح متصلًا عن بعد تلقائيًا؛ استخدم `scripts/START_REMOTE_BOT.ps1 -ApiUrl https://talos-unified-v2.onrender.com` على Windows بعد إيقاف عملية bot القديمة وتوحيد ملف إعداداته. النسخة المنشورة على Render تحتوي الـAPI فقط؛ BotFather token لا يوضع في GitHub، وحالة Telegram لا تُعرض كمتصل إلا بعد اختبار حقيقي. بعض محتويات Command Center بصريًا نموذجية وموسومة ضمن الواجهة؛ الوظائف الحقيقية في التبويبات أعلاه.

قاعدة Render Postgres المجانية الحالية مؤقتة وتنتهي 2026-11-12؛ لا تستخدمها لسجل مالي إنتاجي دون نسخة احتياطية وخطة ترقية. لا تضف NEON القديم.

---

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
