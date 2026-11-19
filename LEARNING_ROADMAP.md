# 🎓 Aegis-Code: Complete Learning Roadmap & Interview Mastery Guide

> **लक्ष्य (Goal):** अगर आपको सिर्फ बेसिक Python आती है, तो इस रोडमैप को फॉलो करके आप **AST Parsing (libcst)**, **LangGraph Orchestration**, **Docker Sandboxing**, **LLM Integration (LangChain)**, और **Modern Python CLI (Typer + Rich)** को स्क्रैच से सीखकर किसी भी इंटरव्यू में इस प्रोजेक्ट को सीनियर इंजीनियर की तरह समझा पाएंगे।

---

## 🗺️ Tech Stack Breakdown (कौन सी तकनीक किस काम आती है?)

| Technology | क्यों यूज़ की गई? (Why in Aegis-Code?) | क्या नया सीखने को मिलेगा? |
| :--- | :--- | :--- |
| **libcst** | Python कोड को बिना रन किए उसका स्ट्रक्चर (AST) पढ़ना व बदलना | Concrete Syntax Tree vs Abstract Syntax Tree, Visitors |
| **LangGraph** | बग्ज़ ढूंढने और फिक्स करने का साइक्लिक (Looping) AI वर्कफ़्लो बनाना | StateGraph, Conditional Edges, TypedDict State |
| **Docker SDK** | अनजान / अनट्रस्टेड टेस्ट केसेस को बिना इंटरनेट और CPU लिमिट में चलाना | Container Isolation, Tar Injection, Resource Limits |
| **Typer + Rich** | टर्मिनल में कलरफुल टेबल्स, बैनर्स और CLI कमांड्स बनाना | Click internals, Type Hints as CLI arguments |
| **SQLite3** | हर स्कैन, टेस्ट फ़ेल्योर और पैच का ऑडिट ट्रेल स्टोर करना | WAL mode, In-memory vs Disk DB, Foreign Keys |
| **LangChain** | Google Gemini / OpenAI के साथ स्ट्रक्चर्ड प्रॉम्प्टिंग और कोड पार्सिंग | ChatModels, System Prompts, Error Recovery |

---

## 📚 फ़ाइल पढ़ने का सही क्रम (Step-by-Step Study Order)

आपको फाइल्स को जटिलता (Complexity) के हिसाब से **5 फेज़ (Phases)** में पढ़ना चाहिए:

```
Phase 1: फाउंडेशन और यूटिलिटीज़ (db.py, helpers.py, logger.py)
   ⬇
Phase 2: कोड को समझना - AST Parsing (parser.py)
   ⬇
Phase 3: टेस्ट जनरेशन और ट्राइएज (generator.py, triage.py)
   ⬇
Phase 4: सैंडबॉक्स और ऑटो-पैचिंग (sandbox.py, patcher.py)
   ⬇
Phase 5: ऑर्केस्ट्रेशन और CLI (graph.py, cli.py)
```

---

### 🟢 Phase 1: Foundations & Utilities (दिन 1 - दिन 2)
*शुरुआत आसान फाइलों से करें ताकि प्रोजेक्ट का डेटा फ्लो और लॉगिंग समझ आ जाए।*

#### 1. [`aegis_code/utils/logger.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/utils/logger.py)
- **क्या सीखोगे:** 
  - Python का इनबिल्ट `logging` मॉड्यूल कैसे कस्टमाइज़ करते हैं।
  - `rich.logging.RichHandler` से रंग-बिरंगे टर्मिनल लॉग्स कैसे बनते हैं।
- **इंटरव्यू कॉन्सेप्ट:** Logger Singletons, Handler Chaining, Avoiding duplicate handlers.

#### 2. [`aegis_code/utils/db.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/utils/db.py)
- **क्या सीखोगे:**
  - SQLite में 3 टेबल कैसे जुड़े हैं: `scan_runs` ➔ `failures` ➔ `patches`
  - `PRAGMA journal_mode=WAL` (Write-Ahead Logging) क्यों जरूरी है कनकूरेंसी के लिए।
- **इंटरव्यू कॉन्सेप्ट:** Relational Schema Design, Foreign Keys, Audit Logs.

#### 3. [`aegis_code/utils/helpers.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/utils/helpers.py)
- **क्या सीखोगे:**
  - `difflib.unified_diff` से `git diff` जैसी लाइन-बाय-लाइन स्ट्रिंग कैसे बनती है।
  - मल्टी-मॉडल रेजोल्यूशन (Google Gemini vs OpenAI auto-switch)।
- **इंटरव्यू कॉन्सेप्ट:** Defensive provider selection, Environment Variable fallbacks.

---

### 🟡 Phase 2: Code Understanding with AST (दिन 3 - दिन 5)
*यह प्रोजेक्ट का सबसे यूनिक और एडवांस पार्ट है।*

#### 4. [`aegis_code/core/parser.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/core/parser.py)
- **क्या सीखोगे:**
  - **AST (Abstract Syntax Tree)** क्या होता है? Python कोड एक ट्री स्ट्रक्चर की तरह कैसे रिप्रेजेंट होता है।
  - `libcst` (Concrete Syntax Tree) और Python के डिफ़ॉल्ट `ast` में अंतर — libcst कमेंट्स और वाइटस्पेस को भी याद रखता है।
  - **Visitor Pattern:** `_AegisVisitor(cst.CSTVisitor)` कैसे फ़ाइल के अंदर घूम-घूम कर:
    - Async फंक्शन्स ढूंढता है (`isinstance(node.asynchronous, cst.Asynchronous)`)
    - डेटाबेस कॉल्स ढूंढता है (`.execute()`, `.commit()`, `.fetchall()`)
    - लूप्स और उनके अंदर `break`/`continue` पकड़ता है।
- **इंटरव्यू सवाल:** *"Why did you use libcst instead of standard `ast` or regex to inspect code?"*
  - **जवाब:** Regex कोड का स्ट्रक्चर (nested loops, scopes) नहीं समझ पाता। libcst पूर्ण ट्री बनाता है और ओरिजिनल कोड फॉर्मेटिंग को बिना खराब किए मॉडिफाई करने की क्षमता देता है।

---

### 🟠 Phase 3: Attack Generation & Error Triage (दिन 6 - दिन 8)

#### 5. [`aegis_code/core/generator.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/core/generator.py)
- **क्या सीखोगे:**
  - AST Parser से मिले डेटा (JSON) को LLM के सिस्टम प्रॉम्प्ट में कैसे इंजेक्ट करते हैं।
  - Adversarial Testing के 8 प्रमुख नियम (None inputs, Huge strings, Type confusion, Race conditions with threading)।
  - LLM के रॉ रिस्पॉन्स (Markdown code block) से सुरक्षित Python कोड एक्सट्रेक्ट करना।
- **इंटरव्यू सवाल:** *"How do you prevent the LLM from writing trivial or mocked tests?"*
  - **जवाब:** सिस्टम प्रॉम्प्ट में सख्त नियम दिए गए हैं: `NEVER import or use unittest.mock to mock the function under test`. हम असली फंक्शन को एक्स्ट्रीम वैल्यूज़ देकर टेस्ट करते हैं।

#### 6. [`aegis_code/core/triage.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/core/triage.py)
- **क्या सीखोगे:**
  - Pytest के रॉ टर्मिनल आउटपुट को Regex से पार्स करके `TriagedFailure` ऑब्जेक्ट बनाना।
  - **बग सेवेरिटी क्लासिफिकेशन (Severity):**
    - `CRITICAL`: 500 एरर्स, मेमोरी करप्शन, डेडलॉक, रेस कंडीशन्स
    - `HIGH`: टाइमआउट (TimeoutError)
    - `MEDIUM`: लॉजिक फ़ेल्योर्स
    - `IGNORED`: अपेक्षित वैलिडेशन एरर्स (HTTP 422, Pydantic ValidationError)
- **इंटरव्यू सवाल:** *"How does your system distinguish between a real crash and an intentional validation error?"*
  - **जवाब:** Triage नोड HTTP 422 और Pydantic/ValueErrors को `Severity.IGNORED` मार्क कर देता है, जिससे AI फालतू के सही कोड को पैच करने में टोकन बर्बाद न करे।

---

### 🔴 Phase 4: Sandboxing & Auto-Patching (दिन 9 - दिन 12)

#### 7. [`aegis_code/core/sandbox.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/core/sandbox.py)
- **क्या सीखोगे:**
  - Python में `docker` SDK (`docker.from_env()`) का उपयोग।
  - **सिक्योरिटी और आइसोलेशन:**
    - `network_disabled=True`: रन होते समय कंटेनर का इंटरनेट पूरी तरह बंद रहता है (ताकि कोई मलेशियस कोड बाहर डेटा न भेज सके)।
    - `cpu_quota` और `mem_limit="512m"`: कंटेनर आपके पूरे कंप्यूटर को हैंग न करे।
  - इन-मेमोरी Tar Archive (`io.BytesIO` + `tarfile`) बनाकर कंटेनर में फाइल इंजेक्ट करना (`put_archive`) बिना डिस्क पर टेम्प फाइलें बनाए।
- **इंटरव्यू सवाल:** *"Why run tests in Docker instead of local subprocess?"*
  - **जवाब:** अनट्रस्टेड कोड सिस्टम फाइल्स डिलीट कर सकता है, इनफाइनाइट लूप में CPU खा सकता है, या इंटरनेट कॉल कर सकता है। डॉकर सैंडबॉक्स हार्ड CPU/मेमोरी लिमिट और नो-नेटवर्क गारंटी देता है।

#### 8. [`aegis_code/core/patcher.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/core/patcher.py)
- **क्या सीखोगे:**
  - ओरिजिनल कोड + स्टैक ट्रेस को LLM को भेजकर सुरक्षित पैच तैयार करवाना।
  - **Syntax Validation Gate:** पैच को डिस्क पर लिखने से पहले `libcst.parse_module()` से चेक करना कि कहीं AI ने सिंटैक्स एरर तो नहीं कर दिया। अगर एरर है, तो दोबारा ठीक करवाना।
  - पैच को डिस्क पर अप्लाई करना और फ़ेल होने पर तुरंत **Rollback / Revert** करना।

---

### 🟣 Phase 5: Orchestration, Testing & CLI (दिन 13 - दिन 15)

#### 9. [`aegis_code/core/graph.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/core/graph.py)
- **क्या सीखोगे:**
  - **LangGraph** क्या है और यह साधारण LangChain चेन्स से बेहतर क्यों है?
  - स्टेट ड्रिवन आर्किटेक्चर (`AegisState: TypedDict`)।
  - साइक्लिक लूप (Cyclic Graph): 
    `Parser` ➔ `Generator` ➔ `Sandbox` ➔ `Triage` ➔ `Patcher` ➔ `Verification` ➔ `(यदि फ़ेल हुआ तो Retries <= 3 वापस Triage)` ➔ `Accept/Diff`.
- **इंटरव्यू सवाल:** *"Why did you choose LangGraph over traditional DAG tools like Airflow or standard while-loops?"*
  - **जवाब:** LangGraph AI एजेंट्स के स्टेट-मैनेजमेंट, कंडीशनल ब्रांचिंग और ह्यूमन-इन-द-लूप / साइक्लिक रिट्राइज़ के लिए सबसे मॉडर्न और लाइटवेट फ़्रेमवर्क है।

#### 10. [`aegis_code/cli.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/aegis_code/cli.py)
- **क्या सीखोगे:**
  - Typer से आधुनिक कमांड-लाइन टूल्स बनाना।
  - `--dry-run`, `--path`, `--verbose`, `--output` फ्लैग्स को हैंडल करना।
  - Rich Panels और Tables से यूजर को सुंदर आउटपुट दिखाना।

#### 11. [`tests/test_parser.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/tests/test_parser.py) & [`tests/test_triage.py`](file:///Users/himanshuyadav/Desktop/Agis_Code/aegis-code/tests/test_triage.py)
- **क्या सीखोगे:**
  - Pytest Fixtures (`tmp_path`, `sample_code`)।
  - यूनिट टेस्ट्स और एज-केस टेस्टिंग का सही तरीका।

---

## 🎯 इंटरव्यू प्रेजेंटेशन: रेज़्युमे और इंटरव्यू में कैसे बोलें?

### 1. प्रोजेक्ट की 30-सेकंड एलिवेटर पिच (Elevator Pitch)
> *"मैंने **Aegis-Code** नाम से एक ऑटोनॉमस AST-ड्रिवन रिग्रेशन और एडवर्सरियल बग-फिक्सर बनाया है। यह कोडबेस को बिना रन किए `libcst` से पार्स करता है, डेटाबेस और असिंक्रोनस कॉल्स को ढूंढता है, और LLM के ज़रिए एडवर्सरियल टेस्ट्स जनरेट करता है। इन टेस्ट्स को आइसोलेटेड डॉकर कंटेनर में बिना नेटवर्क के चलाया जाता है। अगर कोई अनहैंडल्ड क्रैश मिलता है, तो LangGraph बेस्ड साइक्लिक लूप अपने आप उस कोड को पैच करके वेरीफाई करता है और अंत में एक क्लीन Git Diff जनरेट करता है।"*

### 2. रेज़्युमे के लिए 4 बुलेट पॉइंट्स (Resume Bullet Points)
- **Architected an Autonomous Self-Healing Pipeline:** Built an end-to-end regression fixing engine using **LangGraph**, reducing debugging cycles by automatically reproducing and patching edge-case exceptions.
- **AST-Based Static Analysis with LibCST:** Engineered syntax-tree traversal to map async functions, loop boundaries, and ORM/DB invocations to feed contextual metadata to LLM generators.
- **Secure Sandboxed Test Execution:** Leveraged **Docker SDK for Python** to execute aggressive adversarial suites with hard memory limits (512MB), CPU throttling, and disabled container networking.
- **Intelligent Triage & Rollback Mechanism:** Implemented severity filtering to ignore handled 422/ValidationErrors while triggering iterative 3-attempt patch-verify loops with transactional AST validation.

### 3. टॉप 5 तकनीकी इंटरव्यू सवाल (Must Prepare)
1. **Q: डॉकर कंटेनर में नेटवर्क बंद (`network_disabled=True`) क्यों रखा गया?**
   - *A: ताकि जेनरेटेड टेस्ट्स में कोई सुरक्षा जोखिम (Data Exfiltration, Calling External APIs) न हो और टेस्ट्स पूरी तरह डिटरमिनिस्टिक रहें।*
2. **Q: Triage नोड की क्या भूमिका है?**
   - *A: यह नॉइज़ फ़िल्टर करता है। अगर कोई API 422 Unprocessable Entity फेंक रही है तो वह सामान्य है, लेकिन अगर 500 Internal Server Error या Deadlock है तो ही Patcher को कॉल जाता है।*
3. **Q: अगर LLM इनवैलिड सिंटैक्स वाला कोड पैच दे दे तो क्या होगा?**
   - *A: `patcher.py` में `_validate_syntax()` लगा है जो `libcst.parse_module()` चलाकर चेक करता है। गलत सिंटैक्स मिलने पर वह फ़ाइल डिस्क पर नहीं लिखी जाती और AI को सिंटैक्स एरर के साथ री-प्रॉम्प्ट किया जाता है।*
4. **Q: SQLite में WAL (Write-Ahead Logging) क्यों ऑन किया गया?**
   - *A: ताकि साइमल्टेनियस रीड्स और राइट्स एक दूसरे को ब्लॉक न करें और क्रैश की स्थिति में डेटा करप्ट न हो।*
5. **Q: इस पूरे सिस्टम में लूप खत्म होने की गारंटी क्या है?**
   - *A: LangGraph में `MAX_RETRIES = 3` सेट है। 3 कोशिशों के बाद सिस्टम स्वतः `accept_node` पर जाकर अंतिम स्थिति की रिपोर्ट दे देता है, जिससे इनफाइनाइट लूप नहीं बनता।*

---

## 📅 15-दिन का स्टडी शेड्यूल (Study Plan)

- **दिन 1-2:** Phase 1 (Logger, DB, Helpers) समझें और छोटे स्क्रिप्ट्स चलाएं।
- **दिन 3-5:** Phase 2 (Parser और libcst) पर फोकस करें, डमी कोड पार्स करके प्रिंट करें।
- **दिन 6-8:** Phase 3 (Generator और Triage) के प्रॉम्प्ट्स और Regex को समझें।
- **दिन 9-12:** Phase 4 (Docker Sandbox और Patcher) को चलाएं, डॉकर कंटेनर लाइफसाइकल देखें।
- **दिन 13-14:** Phase 5 (LangGraph और CLI) को एंड-टू-एंड डीबग करके समझें।
- **दिन 15:** मॉक इंटरव्यू प्रैक्टिस (एलिवेटर पिच + ऊपर दिए गए 5 सवाल)।
