"""
Shaguf course scraper – Online Course Enrollment Analysis (IT362, King Saud University)

Step 1: Crawl Shaguf course listing pages (with pagination) and each course page,
        extracting title, price, old price, discount, duration, level, rating,
        students, instructor, university, and course code. Saves one CSV.
Step 2: Revisit courses with a missing student count and fill it in.

Requests are throttled (SLEEP_LIST / SLEEP_DETAIL) to avoid overloading the site.
Requirements: pip install requests beautifulsoup4 lxml pandas
"""

# =====================================================================
# STEP 1 – Scrape listing + detail pages
# =====================================================================
import re, json, time, math
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

BASE = "https://shaguf.com"

# ✅ الأنواع المطلوبة (أرقام + سترينق)
TYPES = [1, 2, 4, "qudrat_tahsili"]

MAX_PAGES = 40        # عدد صفحات القائمة لكل type
LIMIT_TOTAL = 2000    # حد أقصى روابط لكل type
SLEEP_LIST = 0.35     # تهدئة بين صفحات القائمة
SLEEP_DETAIL = 0.20   # تهدئة بين صفحات التفاصيل

# حفظ نهائي
OUTPUT_CSV = "shaguf_subjects_types_1_2_4_qudrat_tahsili_FULL.csv"

session = requests.Session()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

# -------------------------
# Utils
# -------------------------
def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def fetch_html(url):
    r = session.get(url, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return r.text

def set_query_param(url, key, value):
    parts = urlparse(url)
    q = parse_qs(parts.query)
    q[key] = [str(value)]
    new_q = urlencode(q, doseq=True)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_q, parts.fragment))

def extract_jsonld(soup):
    objs = []
    for tag in soup.find_all("script", attrs={"type":"application/ld+json"}):
        try:
            data = json.loads(tag.get_text(strip=True))
            if isinstance(data, list):
                objs.extend(data)
            else:
                objs.append(data)
        except:
            pass
    return objs

# -------------------------
# Parsing helpers
# -------------------------
def parse_rating(text):
    if not text: return None
    m = re.search(r"\b([0-5](?:\.\d+)?)\b", text)
    return float(m.group(1)) if m else None

def parse_students(text):
    if not text: return None
    t = text.lower().replace(",", "")
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*([mk])\b", t)
    if m:
        num = float(m.group(1))
        return int(num * (1_000_000 if m.group(2) == "m" else 1_000))
    # English
    m = re.search(r"\b(\d{1,12})\b(?=.*\b(students|learners|enrolled)\b)", t)
    if m:
        return int(m.group(1))
    # Arabic
    m = re.search(r"(\d{1,12})\s*(?:طالب|طلاب|مشترك|مشتركين)", text)
    if m:
        return int(m.group(1))
    return None

def parse_duration(text):
    if not text: return None
    t = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(week|weeks)\b", t)
    if m: return f"{m.group(1)} weeks"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(hour|hours|hr|hrs|h)\b", t)
    if m: return f"{m.group(1)} hours"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(minute|minutes|min|mins)\b", t)
    if m: return f"{m.group(1)} minutes"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ساعة|ساعات)\b", text)
    if m: return f"{m.group(1)} hours"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(دقيقة|دقائق)\b", text)
    if m: return f"{m.group(1)} minutes"
    return None

def parse_level(text):
    if not text: return None
    t = text.lower()
    mapping = {
        "beginner": "Beginner",
        "intermediate": "Intermediate",
        "advanced": "Advanced",
        "all levels": "All Levels",
        "مبتدئ": "Beginner",
        "متوسط": "Intermediate",
        "متقدم": "Advanced",
        "جميع المستويات": "All Levels",
        "سهل": "Beginner",
        "صعب": "Advanced",
    }
    for k,v in mapping.items():
        if k in t:
            return v
    return None

def detect_free_paid(text):
    t = (text or "").lower()
    if any(k in t for k in ["free", "مجاني", "مجانا", "مجانًا"]):
        return "Free"
    if any(k in t for k in ["sar", "usd", "$", "﷼", "ر.س", "ريال", "price", "اشتراك", "رسوم", "رس"]):
        return "Paid"
    return None

def _extract_numbers_from_price(s):
    if not s: return None
    s = s.replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None

def find_prices_in_text(text):
    if not text:
        return (None, None)
    candidates = re.findall(r"(?:SAR|USD|﷼|ر\.س\.|ر\.س|ريال|رس)\s*[\d,.]+|[\d,.]+\s*(?:SAR|USD|﷼|ر\.س\.|ر\.س|ريال|رس)", text, flags=re.I)
    candidates = [clean(c) for c in candidates if clean(c)]
    if len(candidates) >= 2:
        nums = [(_extract_numbers_from_price(c), c) for c in candidates[:3]]
        nums = [(n, c) for n,c in nums if n is not None]
        if len(nums) >= 2:
            unique_nums = {n for n, c in nums}
            if len(unique_nums) >= 2:
                nums_sorted = sorted(nums, key=lambda x: x[0])
                current = nums_sorted[0][1]
                old = nums_sorted[-1][1]
                return current, old
            else:
                return nums[0][1], None
    elif len(candidates) == 1:
        return candidates[0], None
    return (None, None)

def compute_discount_percent(price_str, old_price_str):
    p = _extract_numbers_from_price(price_str)
    o = _extract_numbers_from_price(old_price_str)
    if p is None or o is None or o <= 0:
        return None
    if o <= p:
        return None
    return round((o - p) / o * 100, 2)

def extract_university(text):
    if not text:
        return None

    # محاولة الاستخراج من بين الفواصل (شائع جداً في المنصة)
    m_pipe = re.search(r"\|\s*(جامعة\s+[^\|]+?)\s*\|", text)
    if m_pipe:
        uni = clean(m_pipe.group(1))
        # استبعاد الكلمات الخاطئة
        if not any(b in uni for b in ["شاركنا", "اسم", "حساب", "جامعتك"]):
            return uni

    # تنظيف العبارات التوجيهية للزوار
    t = re.sub(r"شاركنا اسم جامعتك.*?(?=\s|$)", " ", text)
    t = re.sub(r"اسم جامعتك", " ", t)

    # بحث عام عن كلمة جامعة تليها أسماء
    m = re.search(r"(جامعة\s+[\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+){0,4})", t)
    if m:
        uni = clean(m.group(1))
        for bad in ["انضم", "شغوف", "تعلم", "الرئيسية", "تسجيل", "دخول", "إنشاء", "حساب", "شاركنا", "جامعتك"]:
            if bad in uni:
                return None
        return uni
    return None

def extract_course_code(text):
    if not text:
        return (None, None, None)

    # توحيد الأرقام العربية إلى الإنجليزية للتسهيل
    arabic_to_eng = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    t = text.translate(arabic_to_eng).upper()

    # النمط العادي مثل CSC 101, فيز 1103
    m = re.search(r"(?:^|\s|\b|\||-)([A-Z]{2,6}|[أ-ي]{2,5})\s*[-_]?\s*(\d{2,4})(?:\s|$|\b|\||-)", t)
    if m:
        prefix = clean(m.group(1))
        number = clean(m.group(2))
        return (prefix, number, f"{prefix} {number}")

    # النمط المعكوس مثل 101 ادا
    m = re.search(r"(?:^|\s|\b|\||-)(\d{2,4})\s*[-_]?\s*([A-Z]{2,6}|[أ-ي]{2,5})(?:\s|$|\b|\||-)", t)
    if m:
        number = clean(m.group(1))
        prefix = clean(m.group(2))
        return (prefix, number, f"{prefix} {number}")

    return (None, None, None)

def is_exam_prep(text):
    if not text:
        return 0
    kws = [
        "فاينل","نهائي","ميد","ميدترم","منتصف","تلخيص","ملخص",
        "اختبار","تجميعات","مراجعة","حل","نماذج",
        "Final","Midterm","Exam","Revision","Review","Mock"
    ]
    t = text.lower()
    return 1 if any(k.lower() in t for k in kws) else 0

def extract_category_label_from_card(card_text):
    if not card_text:
        return None
    labels = ["قدرات", "تحصيلي", "جامعة", "جامعي", "مدرسة", "ثانوي", "ابتدائي", "متوسط"]
    found = []
    for l in labels:
        if re.search(r'\b' + l + r'\b', card_text):
            found.append(l)

    # لو تم إيجاد عدد كبير من التصنيفات معاً، فذلك يعني أنه التقط قائمة الموقع (Header) بالخطأ
    if len(found) > 3:
        return None
    return " | ".join(dict.fromkeys(found)) if found else None

# -------------------------
# Listing page extraction (cards + urls)
# -------------------------
def extract_subject_cards_from_list_page(list_url):
    html = fetch_html(list_url)
    soup = BeautifulSoup(html, "lxml")

    cards = {}

    for a in soup.select("a[href]"):
        href = a.get("href","")
        if not href:
            continue
        full = urljoin(BASE, href.split("?")[0])
        if not full.startswith(BASE):
            continue

        if ("/subjects/" in full) or ("/subject/" in full):
            if full not in cards:
                cards[full] = {"list_url": list_url}

    for url in list(cards.keys()):
        a_tags = soup.find_all("a", href=True, attrs={"href": lambda x: x and url.replace(BASE, "") in x})

        container_text = ""
        title_guess = None

        if a_tags:
            # نختار الـ a tag الذي يحتوي على النص الأطول ليكون المعبر الأفضل
            a = max(a_tags, key=lambda tag: len(tag.get_text(strip=True)))

            parent = a
            # نصعد للأب لإيجاد الصندوق الكلي، مع التوقف إذا كان الحجم كبيراً جداً (تجنباً لقراءة الموقع كله)
            for _ in range(4):
                if parent.parent and parent.parent.name not in ["body", "html", "main", "header"]:
                    if len(parent.parent.get_text(strip=True)) > 800:
                        break
                    parent = parent.parent
                else:
                    break

            if parent:
                container_text = clean(parent.get_text(" ", strip=True))

            heading = parent.find(["h2", "h3", "h4", "h5", "h6"]) if parent else None
            if heading:
                title_guess = clean(heading.get_text(" ", strip=True))
            else:
                title_guess = clean(a.get_text(" ", strip=True))

        if title_guess and len(title_guess) >= 4:
            cards[url]["title_list"] = title_guess

        price, old_price = find_prices_in_text(container_text)
        cards[url]["price_list"] = price
        cards[url]["old_price_list"] = old_price

        o_num = _extract_numbers_from_price(old_price)
        p_num = _extract_numbers_from_price(price)
        cards[url]["has_discount_list"] = 1 if (o_num is not None and p_num is not None and o_num > p_num) else 0

        cards[url]["discount_percent_list"] = compute_discount_percent(price, old_price)
        cards[url]["free_paid_list"] = detect_free_paid(container_text)
        cards[url]["category_label_list"] = extract_category_label_from_card(container_text)

    return cards

def get_all_subject_cards_for_type(type_value, max_pages=10, limit_total=1000):
    start_url = f"{BASE}/subjects?type={type_value}"
    seen = set()
    ordered = []

    for page in range(1, max_pages+1):
        page_url = set_query_param(start_url, "page", page)
        try:
            cards_map = extract_subject_cards_from_list_page(page_url)
        except Exception as e:
            print(f"[WARN] type={type_value} failed page={page}: {e}")
            continue

        if not cards_map:
            print(f"[INFO] type={type_value} no cards at page={page}. Stop.")
            break

        new_count = 0
        for url, info in cards_map.items():
            if url not in seen:
                seen.add(url)
                ordered.append((url, info))
                new_count += 1
                if len(ordered) >= limit_total:
                    return ordered

        print(f"[LIST] type={type_value} page={page} | found={len(cards_map)} | new={new_count} | total={len(ordered)}")
        time.sleep(SLEEP_LIST)

    return ordered

# -------------------------
# Detail page extraction
# -------------------------
def extract_subject_features(subject_url):
    html = fetch_html(subject_url)
    soup = BeautifulSoup(html, "lxml")
    page_text = clean(soup.get_text(" ", strip=True))

    title = None

    # 1. القراءة من meta og:title للحصول على العنوان النظيف
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        t = clean(og_title["content"])
        if "إنشاء حساب" not in t and "تسجيل" not in t and "شغوف" not in t:
            title = t

    # 2. القراءة من H1 مع التأكد من عدم التقاط زر إنشاء حساب بالخطأ
    if not title:
        h1 = soup.find("h1")
        if h1:
            t = clean(h1.get_text())
            if "إنشاء حساب" not in t and "تسجيل" not in t:
                title = t

    # JSON-LD extraction
    jsonlds = extract_jsonld(soup)
    j_rating = None
    j_desc = None
    j_instr = None

    if not title:
        for obj in jsonlds:
            if isinstance(obj, dict) and obj.get("@type") in ["Course", "Product"] and obj.get("name"):
                t = clean(obj.get("name"))
                if "إنشاء حساب" not in t and "تسجيل" not in t:
                    title = t
                    break

    for obj in jsonlds:
        if not isinstance(obj, dict):
            continue

        agg = obj.get("aggregateRating")
        if isinstance(agg, dict) and j_rating is None:
            rv = agg.get("ratingValue") or agg.get("rating")
            try:
                j_rating = float(rv) if rv is not None else None
            except:
                pass

        if j_desc is None and obj.get("description"):
            j_desc = clean(obj.get("description"))

        auth = obj.get("author") or obj.get("creator") or obj.get("instructor")
        if j_instr is None and auth:
            if isinstance(auth, dict):
                j_instr = clean(auth.get("name"))
            elif isinstance(auth, list) and auth:
                if isinstance(auth[0], dict):
                    j_instr = clean(auth[0].get("name"))
                else:
                    j_instr = clean(str(auth[0]))
            else:
                j_instr = clean(str(auth))

    # Description (Unstructured)
    description = j_desc
    if not description:
        meta = soup.find("meta", attrs={"name":"description"})
        if meta and meta.get("content"):
            description = clean(meta["content"])
        else:
            for sel in ["#description", ".description", "[class*='description']", "article", "main", "section"]:
                el = soup.select_one(sel)
                if el:
                    txt = clean(el.get_text(" ", strip=True))
                    if len(txt) >= 120:
                        description = txt
                        break
    if not description:
        description = page_text[:1600]

    # 3. الاعتماد على الوصف لاستخراج العنوان كخيار أخير إذا كان العنوان غائباً
    if not title and description:
        m = re.search(r"مادة\s+(.*?)\s+\|", description)
        if m:
            title = clean(m.group(1))

    # استخراج اسم المدرب من النص كخيار بديل في حال لم يكن موجوداً بـ JSON-LD
    instructor = j_instr
    if not instructor:
        m_inst = re.search(r"(?:المعلم|المعلمة|الاستاذ|الاستاذة|أ\.|د\.|الدكتور|الدكتورة)\s+([A-Za-z\u0600-\u06FF\s.]+?)\s+(?:مادة|دورة|كورس|شرح)", description or page_text)
        if m_inst:
            instructor = clean(m_inst.group(1))

    if not instructor:
        for sel in ["[class*='instructor']", "[class*='teacher']"]:
            el = soup.select_one(sel)
            if el:
                cand = clean(el.get_text(" ", strip=True))
                if 2 <= len(cand) <= 120:
                    instructor = cand
                    break

    # Parse features from text
    rating = j_rating if j_rating is not None else parse_rating(page_text)
    students = parse_students(page_text)
    duration = parse_duration(page_text)
    level = parse_level(page_text)

    # Prices from full page text (fallback)
    price, old_price = find_prices_in_text(page_text)
    has_discount = 1 if (price and old_price and compute_discount_percent(price, old_price) is not None) else 0
    discount_percent = compute_discount_percent(price, old_price)

    free_paid = detect_free_paid(page_text)
    if price and not free_paid:
        free_paid = "Paid"

    # Extra DS features
    university = extract_university(page_text) or extract_university(description) or extract_university(title)
    prefix, number, full_code = extract_course_code((title or "") + " " + (description or ""))
    exam_flag = is_exam_prep((title or "") + " " + (description or ""))

    return {
        "url": subject_url,
        "title": title,
        "rating": rating,
        "students": students,
        "duration": duration,
        "level": level,
        "free_paid": free_paid,
        "price": price,
        "old_price": old_price,
        "has_discount": has_discount,
        "discount_percent": discount_percent,
        "instructor": instructor,
        "description": description,
        "university": university,
        "course_code_prefix": prefix,
        "course_code_number": number,
        "course_code_full": full_code,
        "is_exam_prep": exam_flag,
    }

# -------------------------
# RUN ALL TYPES -> ONE CSV
# -------------------------
all_rows = []
global_seen = set()

for t in TYPES:
    print("\n==============================")
    print("START type =", t)

    cards = get_all_subject_cards_for_type(t, max_pages=MAX_PAGES, limit_total=LIMIT_TOTAL)
    print("Total URLs for type", t, ":", len(cards))

    for idx, (url, card_info) in enumerate(cards, 1):
        if url in global_seen:
            continue
        global_seen.add(url)

        try:
            detail = extract_subject_features(url)
            row = {
                "type_value": str(t),
                "rank_in_type": idx,
                "list_page": card_info.get("list_url"),
                "title_list": card_info.get("title_list"),
                "category_label": card_info.get("category_label_list"),
                "price_list": card_info.get("price_list"),
                "old_price_list": card_info.get("old_price_list"),
                "has_discount_list": card_info.get("has_discount_list"),
                "discount_percent_list": card_info.get("discount_percent_list"),
                "free_paid_list": card_info.get("free_paid_list"),
            }
            # merge detail
            row.update(detail)

            # If detail missing, use list
            if not row.get("price") and row.get("price_list"):
                row["price"] = row["price_list"]
            if not row.get("old_price") and row.get("old_price_list"):
                row["old_price"] = row["old_price_list"]
            if row.get("has_discount") in [None, 0] and row.get("has_discount_list") == 1:
                row["has_discount"] = 1
            if row.get("discount_percent") is None and row.get("discount_percent_list") is not None:
                row["discount_percent"] = row["discount_percent_list"]
            if not row.get("free_paid") and row.get("free_paid_list"):
                row["free_paid"] = row["free_paid_list"]
            if not row.get("title") and row.get("title_list"):
                row["title"] = row["title_list"]

            # last chance university/code
            if not row.get("university"):
                row["university"] = extract_university(row.get("title")) or extract_university(row.get("description"))
            if not row.get("course_code_full"):
                p, n, f = extract_course_code(row.get("title") or row.get("description"))
                row["course_code_prefix"], row["course_code_number"], row["course_code_full"] = p, n, f

            all_rows.append(row)

        except Exception as e:
            all_rows.append({
                "type_value": str(t),
                "rank_in_type": idx,
                "url": url,
                "list_page": card_info.get("list_url"),
                "error": str(e)
            })

        time.sleep(SLEEP_DETAIL)

df = pd.DataFrame(all_rows)
print("\nDF shape:", df.shape)
df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
print(df.head(10))


# =====================================================================
# STEP 2 – Fill in missing student counts
# =====================================================================
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import time

# إعدادات الملفات
INPUT_FILE = "shaguf_subjects_types_1_2_4_qudrat_tahsili_FULL.csv"
OUTPUT_FILE = "shaguf_updated_with_students.csv"

# إعدادات الاتصال
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

def parse_students_advanced(text):
    if not text: return None

    # 1. تحويل الأرقام العربية إلى إنجليزية
    arabic_to_eng = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    t = text.translate(arabic_to_eng).lower().replace(",", "")

    # 2. البحث عن أنماط K و M
    m_km = re.search(r"(\d+(?:\.\d+)?)\s*([mk])", t)
    if m_km:
        num = float(m_km.group(1))
        return int(num * (1_000_000 if m_km.group(2) == "m" else 1_000))

    # 3. البحث عن كلمات تدل على الطلاب
    m_ar = re.search(r"(\d+)\s*(طالب|طلاب|مشترك|مشتركين|متدرب|enrolled|students)", t)
    if m_ar:
        return int(m_ar.group(1))

    # 4. محاولة التقاط رقم وحيد معزول
    m_simple = re.search(r"\b(\d+)\b", t)
    if m_simple:
        return int(m_simple.group(1))

    return None

def get_student_count(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)

        # التصحيح هنا: استخدام status_code بدلاً من status_status
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "lxml")

        # محاولة إيجاد الأيقونة
        student_icon = soup.find("i", class_=lambda x: x and ("fa-users" in x or "fa-user" in x))
        if student_icon:
            text = student_icon.parent.get_text(strip=True)
            count = parse_students_advanced(text)
            if count is not None: return count

        # البحث في كامل النص
        page_text = soup.get_text(" ", strip=True)
        return parse_students_advanced(page_text)

    except Exception as e:
        print(f"\nError fetching {url}: {e}")
        return None

# --- بدء عملية التحديث ---

print("جاري تحميل الملف...")
df = pd.read_csv(INPUT_FILE)

# استهداف الصفوف التي لم يسحب عدد طلابها (التي قيمتها NaN)
mask = df['students'].isna()
urls_to_update = df[mask]

print(f"تم العثور على {len(urls_to_update)} سجل يحتاج لتحديث.")

count_updated = 0
for index, row in urls_to_update.iterrows():
    url = row['url']
    print(f"[{count_updated+1}/{len(urls_to_update)}] جاري فحص: {url}", end="\r")

    students = get_student_count(url)
    if students is not None:
        df.at[index, 'students'] = students

    count_updated += 1

    # تهدئة وحفظ دوري
    time.sleep(0.4)
    if count_updated % 10 == 0:
        df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

# الحفظ النهائي
df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
print(f"\n\nتم الانتهاء! الملف الجديد محفوظ باسم: {OUTPUT_FILE}")