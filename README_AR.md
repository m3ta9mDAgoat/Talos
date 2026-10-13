# Talos Unified v2.5 — Full Connection Bridge

هذه الحزمة لا ترفع التوكنات إلى GitHub أو تخزنها في Render. يقدم Talos Research API + PostgreSQL + Hosted reference worker على Render، ويصل إليه بوت Telegram وWindows Worker عبر HTTPS من جهاز Windows. نسخة Render الحالية ستكون v2.3 إلى أن تنشر هذا المصدر.

## خطوة واحدة للنشر وربط خدمات جهازك

بعد فك الضغط إلى `C:\TalosEngines\TalosDeployV25` بحيث يظهر مجلد `scripts` مباشرة:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\TalosEngines\TalosDeployV25\scripts\PUBLISH_AND_CONNECT.ps1"
```

1. اكتب `REPLACE` لرفع النسخة الجديدة إلى المستودع الحالي `m3ta9mDAgoat/Talos` وتفعيل النشر التلقائي على `talos-unified-v2`.
2. ينتظر المشغّل ظهور الإصدار `2.5.0` من `https://talos-unified-v2.onrender.com/health`.
3. أدخل `TALOS_LAB_ADMIN_TOKEN` ثم `TALOS_LAB_WORKER_TOKEN` من Render، **محليًا في نافذة PowerShell فقط**.
4. أغلق نافذة البوت القديم (حتى لا يصبح هناك poller ثانٍ لنفس BotFather token)، ثم اكتب `STOPPED`.
5. تظهر نافذتان جديدتان تلقائيًا: Telegram bot + Windows worker. سينتظر المشغّل حالة `online` الصحيحة من الخادم ولا يدّعي النجاح إذا لم يصل heartbeat.

**شرط:** يوجد إعداد بوتك الذي فعلته مسبقًا في `C:\TalosEngines\TalosBotV2\bot_config.json` وفيه `bot_token` و`owner_user_ids` الصحيحة، وLEAN/Nautilus Stage3 منصوبان إن أردت اختبار التكافؤ الأصلي. إذا لم يعمل المشغّل، راجع شاشة الخطأ ولا ترسل التوكن.

لو رفعت المصدر مسبقًا، لتشغيل الاتصالات بدون رفع جديد:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\TalosEngines\TalosDeployV25\scripts\CONNECT_ALL_REMOTE.ps1"
```

## ما يعمل عند نجاح هذا التفعيل
- موقع Phase 2 v2.5، Admin auth، إدارة البيانات التاريخية CSV، مهام Backtest المرجعية، Walk-forward وMonte Carlo وRobustness، نتائج محفوظة بـPostgreSQL على Render.
- زر Deployments → Run Stage3 parity يرسل طلب parity إلى Windows Worker فقط إذا أبلغ عن تثبيت مرحلة Stage3 واتصاله الفعلي.
- عامل الأبحاث المرجعي داخل Render. عامل Windows منفصل ويتصل عبر HTTPS؛ حالة الاتصال ظاهرة بشكل حقيقي في Deployments / Telegram Bot.
- بوت Telegram الذي تملكه: أوامر البحث، إشعارات نتيجة الوظائف، استيراد منشورات القنوات المصرح بها والرسائل المعاد توجيهها، وأرشيف Replay. يستمر فقط طالما نافذة عملية Windows تعمل والإنترنت متصل.
- Paper account تجريبي فقط. إرسال أوامر وسيط حقيقية ممنوع HTTP 403.

## ليس ادعاءً بالتكامل
- **LEAN/NautilusTrader**: مسار Stage3 المحدد فقط (AAPL/MSFT monthly-close synthetic parity)؛ ليس تنفيذًا عامًا لأي استراتيجية ولا خدمة تعمل داخل Render.
- **Telegram**: bot token في جهازك، وليس خدمة Render مستقلة 24/7. لا يمكن للتطبيق ضمان وصول التنبيهات إذا أغلق جهازك أو الإنترنت. لا توجد إشعارات تلقائية لرصد كل الفرص الرابحة والخاسرة قبل توفر بيانات مصدر سوق مؤرخة وموثوقة.
- **Market data**: البيانات الاصطناعية للاختبارات، أو CSV يرفعه المستخدم. لا تزعم الحزمة تفعيل feed مدفوع أو وصولًا لحظيًا تلقائيًا.
- **Phanes**: يدعم Excel الرسمي يدويًا وليس مراسلة البوت المحمي آليًا.
- **Database**: Render free PostgreSQL تنتهي 2026-11-12؛ يجب النسخ الاحتياطي والترقية لحل دائم قبل الموعد. لم نرحل بيانات Neon القديمة.
- **Live trading**: غير مفعّل، ولا مفاتيح تداول أو اعتماد لوسيط داخل الحزمة.

## الأمان
لا ترفع `.env`, `.local_auth.json`, `bot_config.json`, `.bot_runtime.sqlite3`, أو مفاتيح البوت إلى GitHub. مفاتيح Admin وWorker تُطلب بشكل مخفي ولا تُكتب في الملفات أو وسيطات العملية. حالة ربط البوت والعامل تتحدث من تسجيل وصول موثق، وليست أرقامًا متخيلة.
