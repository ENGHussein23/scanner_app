<div dir="rtl" align="right">

<div align="center">

# HomsScanner — دليل المطوّر

**خدمة مسح ضوئي محلية بواجهة ويب، مصمّمة لتُضمَّن داخل تطبيقك (ويب أو سطح مكتب).**

[English](DEVELOPERS.md) · العربية

</div>

HomsScanner خدمة صغيرة بلغة Python (Flask) بتشتغل عالكمبيوتر اللي عليه السكانر. بتتواصل مع السكانر (WIA أو TWAIN أو eSCL/AirScan عالشبكة)، وبتعرض واجهة ويب عالعنوان `http://127.0.0.1:17650`، وبتحوّل الصفحات الممسوحة لملف PDF. نفس الواجهة بتنفتح بالمتصفح، أو **بتتضمّن داخل تطبيقك** (`<iframe>` أو Flutter Web أو WebView2 …) والتطبيق بيستلم الـ PDF عن طريق `postMessage`.

![صفحة المسح](docs/images/01-scan-page.ar.png)

## المزايا

- **الاتصال بالسكانر**: USB عبر WIA (مدمج بويندوز) أو TWAIN، والسكانرات الشبكية عبر eSCL / AirScan (اكتشاف تلقائي بـ mDNS أو عنوان IP يدوي).
- **خيارات المسح**: الدقة (100–600)، ملوّن / رمادي / أبيض وأسود، سطح زجاجي أو تغذية تلقائية، **وجهين** (duplex)، اختيار السكانر.
- **محرّر الصفحات**: تحديد، تدوير، حذف كل صفحة مع **تراجع**، **إعادة ترتيب** بالسحب والإفلات، عارض ملء الشاشة مع تكبير وتحريك.
- **PDF**: تسمية الملف، تنزيله، أو حفظه عالسطح المكتب. بوضع التضمين بيتسلّم للتطبيق المضيف.
- **متعدد اللغات**: الإنكليزية (الافتراضية) والعربية، والواجهة بتتبدّل تلقائياً بين **LTR و RTL**.
- **ثيم فاتح / داكن** بيقدر التطبيق المضيف يتحكم فيه.
- **تطبيق بشريط المهام**: تشغيل مع ويندوز، صفحة إعدادات، وبروتوكول `homsscan://start` ليشغّل الموقع الخدمة.
- **خصوصية**: بتسمع عالجهاز المحلي فقط، بترفض أي `Host`/`Origin` غريب، والمواقع اللي بتحددها بس بتقدر تضمّنها.
- بتتغلّف بملف تثبيت واحد (PyInstaller + Inno Setup)؛ الموظفين ما بيحتاجوا Python.

| الصفحات | العارض | مضمّنة بتطبيق مضيف |
|---|---|---|
| ![الصفحات](docs/images/04-pages.ar.png) | ![العارض](docs/images/06-viewer.ar.png) | ![مضمّنة](docs/images/09-embedded.ar.png) |

## كيف بتشتغل

![المعمارية](docs/images/architecture.ar.svg)

الخدمة هي اللي بتملك الجهاز وتخزين الصفحات، والواجهة ملفات HTML/CSS/JS عادية بتقدّمها نفس العملية، فما في خطوة بناء للواجهة.

## تشغيل سريع (من الكود)

المتطلبات: Python 3.10 أو أحدث.

```bash
python -m venv .venv
. .venv/bin/activate            # ويندوز: .venv\Scripts\activate
pip install -r requirements.txt
python service_main.py --demo   # سكانر وهمي، بدون جهاز
```

افتح <http://127.0.0.1:17650>. عويندوز اعمل دبل كليك على `run.bat` (مع `--demo` للسكانر الوهمي).

خيارات سطر الأوامر لـ `service_main.py`:

| الخيار | المعنى |
|---|---|
| `--demo` | سكانر وهمي (بدون جهاز) |
| `--background` | تشغيل هادئ (للتشغيل التلقائي و`homsscan://start`) |
| `--settings` | فتح صفحة الإعدادات |
| `--no-tray` | بدون أيقونة شريط المهام |

### السكانرات الحقيقية

1. **WIA** (الافتراضي عويندوز): بدو `pywin32`؛ لازم يظهر السكانر ببرنامج *Windows Scan* / *Fax and Scan*.
2. **TWAIN** (اختياري): `pip install -r requirements-twain.txt`. درايفر TWAIN بـ 32-bit بدو Python 32-bit.
3. **الشبكة (eSCL / AirScan)**: HP وCanon وEpson وBrother ومعظم موديلات ما بعد 2015. بيلاقيها mDNS، أو اكتب الـ IP بالإعدادات.

استعمل زر **فحص الأجهزة المتاحة** بالواجهة (أو `GET /api/devices`) لتشوف شو انلقى.

![فحص الأجهزة](docs/images/03-device-check.ar.png)

## بناء ملف التثبيت لويندوز

على كمبيوتر ويندوز فيه Python 3.10+ (و[Inno Setup 6](https://jrsoftware.org/isinfo.php) اختيارياً):

```bat
build_windows.bat
```

الناتج: `dist\HomsScanner\HomsScanner.exe`، وإذا Inno Setup مثبّت كمان `dist\HomsScanner-Setup.exe` — الملف الوحيد اللي بتعطيه للمستخدمين. المثبّت بيسجّل التشغيل التلقائي وبروتوكول `homsscan://`. مجلد `static/` (مع `static/locales/`) بيتضمّن عن طريق `packaging/HomsScanner.spec`.

## الإعدادات

الإعدادات بالملف `%APPDATA%\HomsScanner\config.json` (و`~/.config/HomsScanner` عغير ويندوز). عدّلها من أيقونة الشريط ← **الإعدادات** أو من <http://127.0.0.1:17650/settings> (ما بتنفتح داخل iframe).

![الإعدادات](docs/images/10-settings.ar.png)

| المفتاح | الافتراضي | ملاحظات |
|---|---|---|
| `port` | `17650` | بدو إعادة تشغيل بعد التغيير |
| `language` | `"auto"` | `auto` أو `en` أو `ar` |
| `autostart` | `true` | تشغيل مع ويندوز |
| `allowed_origins` | `["http://localhost:*", "http://127.0.0.1:*"]` | المواقع المسموح تضمّن الواجهة؛ **ضيف موقع الإنتاج** |
| `defaults` | دقة 300، ملوّن، سطح زجاجي، وجه واحد، اتصال تلقائي | خيارات المسح الأولية |

## اللغات (i18n)

الإنكليزية هي الافتراضية. ترتيب اختيار اللغة:

- **الخادم** (رسائل الـ API): ترويسة `X-Lang` ‹ `?lang=` ‹ `Accept-Language` ‹ `config.language` (`auto` = لغة ويندوز) ‹ `en`.
- **الواجهة**: `?lang=` (بيحطها التطبيق المضيف؛ وقتها بيختفي المحدّد) ‹ اختيار المستخدم (`localStorage`) ‹ إعداد الخدمة ‹ لغة المتصفح ‹ `en`.

الاتجاه (`ltr`/`rtl`) بيتبع اللغة. النصوص بالملفات `static/locales/<code>.json` ومشتركة بين Python والمتصفح. أخطاء الـ API فيها كمان حقل `code` ثابت (مفتاح الرسالة) ليترجمها تطبيقك بنفسو.

### إضافة لغة

1. انسخ `static/locales/en.json` إلى `static/locales/<code>.json` وترجم القيم (خلّي `{placeholders}` متل ما هي؛ مدخلات الجمع بتستعمل `zero/one/two/few/many/other`).
2. بـ `scanner/i18n.py`: ضيف الرمز إلى `LANGUAGES` (وإلى `RTL` إذا اللغة من اليمين لليسار).
3. بـ `static/i18n.js`: ضيفو إلى `LANGS` (`{ name, dir }`). إذا قواعد الجمع مختلفة عدّل `plural_form` (بايثون) ونظيرها بالـ JS.
4. `python -m pytest tests/test_i18n.py` بيتأكد إنو المفاتيح والـ placeholders متطابقة بكل اللغات.

## تضمينها بتطبيقك

حمّل الواجهة واستمع لرسائلها:

```
http://127.0.0.1:17650/?embed=1&theme=dark|light&parent=<origin-تطبيقك>&lang=en|ar
```

| الوسيط | المعنى |
|---|---|
| `embed=1` | تخطيط التضمين؛ الـ PDF بيتبعت للمضيف بدل ما ينزّل |
| `theme` | `dark` أو `light` |
| `parent` | origin صفحتك، هدف الـ `postMessage` (وضع iframe) |
| `lang` | `en` / `ar`؛ بيثبّت اللغة وبيخفي المحدّد |

![مخطط التضمين](docs/images/embed-flow.svg)

**الرسائل** (من الواجهة ← تطبيقك؛ الحمولة **نص** JSON):

| `type` | الحقول | متى |
|---|---|---|
| `homsscan:ready` | `version`, `lang` | خلّصت الواجهة تحميل |
| `homsscan:pdf` | `name`, `pages`, `size`, `base64` | ضغط المستخدم *حفظ وإرفاق* |
| `homsscan:cancel` | – | أغلق المستخدم / ألغى |

### تطبيق ويب عام (أي إطار عمل)

1. من **الإعدادات ← «المواقع المسموح لها بعرض صفحة المسح»** ضيف origin موقعك، مثلاً `https://docs.example.org` (هيك بيتحكم بـ CSP `frame-ancestors`؛ مسموح `http://localhost:*`).
2. ضمّن الـ iframe واستمع:

```html
<iframe id="scanner" allow="" style="width:100%;height:720px;border:0"></iframe>
<script>
  const SERVICE = "http://127.0.0.1:17650";
  const frame = document.getElementById("scanner");
  frame.src = `${SERVICE}/?embed=1&theme=light&lang=ar&parent=${encodeURIComponent(location.origin)}`;

  window.addEventListener("message", (e) => {
    if (e.origin !== SERVICE) return;               // تحقّق دايماً من المرسِل
    const msg = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
    if (msg.type === "homsscan:pdf") {
      const bytes = Uint8Array.from(atob(msg.base64), c => c.charCodeAt(0));
      const file = new File([bytes], msg.name, { type: "application/pdf" });
      // ارفع الملف، أرفقو بنموذج، إلخ
    }
  });
</script>
```

اختيارياً تأكد أولاً إنو الخدمة شغّالة (`GET /api/health`) وإنو موقعك مسموح (`GET /api/embed/check?origin=<origin>`)؛ وإذا مو شغّالة افتح `homsscan://start` لتشغيلها.

### Flutter

التكامل الجاهز موجود بالمجلد `lib/services/scanner_embed/` بمشروع الـ Flutter (مو جزء من كود بايثون هون):

- **Flutter Web**: `<iframe>` عبر `HtmlElementView` (`scanner_embed_web.dart`).
- **ويندوز Desktop**: `webview_windows` / WebView2 (`scanner_embed_io.dart`)؛ الرسائل بتوصل عبر `chrome.webview.postMessage` فما بتحتاج `parent`.
- ضيف `webview_windows: ^0.4.0` إلى `pubspec.yaml`، واستعمل الـ widget `ScannerEmbedView` (`baseUrl`, `darkTheme`, `onMessage`). مرّر `languageCode: Get.locale?.languageCode` لتتبع الواجهة لغة التطبيق (بيصير `lang=`).
- تغيير المنفذ أو العنوان بـ `homs_scanner_config.dart`.

### مضيفات أخرى

أي شي بيعرض صفحة ويب بيشتغل: **WebView2** (WPF/WinForms/.NET MAUI — اقرأ رسائل `window.chrome.webview` عبر `WebMessageReceived`)، أو Electron/Tauri، أو WebView أندرويد/iOS. الخدمة بتقبل localhost فقط، فلازم تشتغل عنفس الجهاز. بالـ WebView (بدون iframe) الرسائل بتنبعت بـ `window.chrome.webview.postMessage(text)`.

### ملاحظات أمنية

- الخدمة بتجاوب بس عالطلبات اللي `Host` و`Origin` فيها `localhost`/`127.0.0.1`.
- بس الـ origins الموجودة بـ `allowed_origins` بتقدر تضمّن الصفحة؛ وصفحة الإعدادات ما بتتضمّن أبداً.
- تحقّق من `event.origin` ومن `type` الرسالة بتطبيقك، واعتبر الـ PDF مدخلاً غير موثوق.

## واجهة HTTP API (ملخّص)

| الطلب والمسار | الغرض |
|---|---|
| `GET /api/health`, `/api/config`, `/api/devices` | الحالة، الافتراضيات، السكانرات المكتشفة |
| `POST /api/scan` · `GET /api/jobs/<id>` · `POST /api/jobs/<id>/cancel` | المسح ومتابعة التقدّم |
| `GET /api/pages` · `/api/pages/<id>/image` · `/thumb` | الصفحات الممسوحة |
| `POST /api/pages/rotate` `delete` `reorder` `trash` `restore` `clear` | تحرير الصفحات |
| `POST /api/pdf` · `GET /api/pdf/<name>` · `POST /api/pdf/<name>/desktop` | بناء / تنزيل / حفظ عالسطح المكتب |
| `GET/POST /api/settings` | الإعدادات (محجوبة داخل iframe) |

ابعت `X-Lang: en|ar` لتستلم رسائل مترجمة.

## التطوير

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest            # اختبارات الوحدة والـ API بما فيها i18n
python service_main.py --demo --no-tray
```

هيكل المشروع:

```
app.py                 تطبيق Flask: الـ API، CSP، حماية localhost
service_main.py        نقطة الدخول (الشريط، التشغيل التلقائي، خيارات CLI)
scanner/               الواجهات الخلفية (wia, twain, escl, demo)، المهام، الإعدادات، i18n، الشريط
static/                index.html, settings.html, app.js, settings.js, i18n.js, style.css
static/locales/        en.json, ar.json
tests/                 اختبارات pytest
packaging/             PyInstaller spec، سكربت Inno Setup، الأيقونة
docs/images/           صور وشروحات الـ README
```

ملاحظات: لا تكتب نصاً ظاهراً للمستخدم مباشرة بالكود — ضيف مفتاحاً لكل ملفات `static/locales/*.json` (الاختبارات بتفرض هالشي). استعمل خصائص CSS المنطقية (`margin-inline-start` …) لتضل الواجهة تشتغل بـ RTL.

## حل المشاكل

| المشكلة | الحل |
|---|---|
| «ما لقيت أي سكانر» | افتح **فحص الأجهزة المتاحة**. تأكد إنو السكانر بيظهر ببرنامج Windows Scan. |
| «مكتبة pywin32 مو مثبتة» | شغّل `run.bat` من جديد، أو `pip install pywin32`. |
| بس TWAIN بيشتغل | `pip install -r requirements-twain.txt` (طابق 32/64-bit مع الدرايفر). |
| الصفحة المضمّنة فاضية | ضيف موقعك لقائمة المواقع المسموحة بالإعدادات ثم أعد التحميل. |
| الخدمة مو شغّالة | افتح `homsscan://start` أو شغّل تطبيق الشريط. |

## المساهمة والترخيص

المشاكل (Issues) وطلبات الدمج مرحّب فيها؛ شغّل `python -m pytest` قبل ما تبعت. لسا ما انختار ترخيص — ضيف ملف `LICENSE` قبل النشر.

شوف كمان [`INTEGRATION.md`](INTEGRATION.md) لملاحظات النشر.

</div>
