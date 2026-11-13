"""우리집 약상자 | streamlit run app.py
GitHub에 app.py, requirements.txt 업로드 후 Streamlit Community Cloud 연결.
선택 Secrets: APP_URL = "https://본인앱.streamlit.app"
사진 자동 인식 없음. 사진은 서버 SQLite에 저장되며 서버 초기화 시 소실 가능.
로그인 없는 수행평가용 시제품입니다.
"""
import base64
import html
import io
import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path

import qrcode
import requests
import streamlit as st

st.set_page_config(page_title="우리집 약상자", page_icon="💊", layout="centered", initial_sidebar_state="collapsed")
DB = Path(__file__).with_name("medicine.db")
MAX_ITEMS = 200


def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except (FileNotFoundError, KeyError):
        return default


def connect():
    con = sqlite3.connect(DB, timeout=20)
    con.execute("CREATE TABLE IF NOT EXISTS medicines (id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL, expiry TEXT NOT NULL, note TEXT NOT NULL)")
    return con


def records():
    with connect() as con:
        return [dict(zip(("id", "name", "location", "expiry", "note"), row)) for row in con.execute("SELECT * FROM medicines ORDER BY expiry, name")]


def save(item):
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO medicines VALUES (?,?,?,?,?)", tuple(item[k] for k in ("id", "name", "location", "expiry", "note")))


def status(expiry):
    days = (date.fromisoformat(expiry) - date.today()).days
    if days < 0:
        return "사용기한 지남", "기한 경과 · 사용하지 마세요", "#b42318", days
    if days <= 30:
        return "30일 이내", f"사용기한까지 {days}일", "#9a6700", days
    return "보관 중", f"사용기한까지 {days}일", "#087a67", days



SYMPTOMS = ['두통', '발열', '근육통', '생리통', '콧물', '코막힘', '재채기', '기침', '가래', '인후통', '복통', '소화불량', '속쓰림', '설사', '변비', '가려움', '철 결핍성 빈혈']


def label_tags(medicine_id):
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS medicine_labels (id TEXT PRIMARY KEY, tags TEXT NOT NULL)")
        row = con.execute("SELECT tags FROM medicine_labels WHERE id=?", (medicine_id,)).fetchone()
    if not row:
        return []
    try:
        values = json.loads(row[0])
        return [x for x in values if x in SYMPTOMS] if isinstance(values, list) else []
    except (ValueError, TypeError):
        return []


def save_tags(medicine_id, tags):
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS medicine_labels (id TEXT PRIMARY KEY, tags TEXT NOT NULL)")
        con.execute("INSERT OR REPLACE INTO medicine_labels VALUES (?,?)", (medicine_id, json.dumps(tags, ensure_ascii=False)))


def find_label_matches(items, requested, today=None):
    today = today or date.today()
    return [(item, sorted(set(requested) & set(label_tags(item["id"]))))
            for item in items if date.fromisoformat(item["expiry"]) >= today
            and set(requested) & set(label_tags(item["id"]))]


def pair_records():
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS medicine_pairs (a TEXT NOT NULL, b TEXT NOT NULL, level TEXT NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(a,b))")
        return [dict(zip(("a", "b", "level", "reason", "source"), row)) for row in con.execute("SELECT * FROM medicine_pairs")]


def save_pair(a, b, level, reason, source):
    if a == b:
        raise ValueError("같은 약끼리는 등록할 수 없습니다.")
    pair_records()
    a, b = sorted((a, b))
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO medicine_pairs VALUES (?,?,?,?,?)", (a, b, level, reason, source))


def pair_fields(current_id=None):
    others = {r["id"]: r["name"] for r in records() if r["id"] != current_id}
    st.markdown("**함께 복용 주의할 약 · 선택**")
    targets = st.multiselect("주의 대상 약", list(others), format_func=lambda k: others[k], help="지금 등록된 약 중 선택합니다. 먼저 등록할 약이 없으면 나중에 정보 수정에서 추가하세요.")
    level = st.selectbox("확인한 주의 수준", ["함께 복용 시 주의", "병용 금지"])
    source = st.selectbox("확인 출처", ["선택하세요", "제품 설명서", "약사에게 확인", "의사에게 확인"])
    reason = st.text_input("확인한 이유·상세 메모", max_chars=300, placeholder="추측하지 말고 확인받은 내용만 입력")
    st.caption("선택한 모든 대상에 같은 수준·출처·메모를 적용합니다. 조합마다 다르면 각각 등록하세요. 기존 동일 조합은 갱신됩니다.")
    return targets, level, source, reason


def valid_pair_fields(fields):
    targets, level, source, reason = fields
    return not targets or (source != "선택하세요" and bool(reason.strip()))


def store_pair_fields(medicine_id, fields):
    targets, level, source, reason = fields
    for target in targets:
        save_pair(medicine_id, target, level, reason.strip(), source)


def show_pair_alerts(found):
    names = {item["id"]: item["name"] for item, _ in found}
    if not names:
        return
    matched = [r for r in pair_records() if r["a"] in names and r["b"] in names]
    if matched:
        st.warning("⚠️ 등록된 병용 주의 조합 — 복용 전 약사에게 확인하세요")
        if any(r["level"] == "병용 금지" for r in matched):
            st.error("🚨 ‘병용 금지’로 등록된 조합은 함께 복용하지 마세요.")
        for r in matched:
            forbidden = r["level"] == "병용 금지"
            accent = "#b42318" if forbidden else "#9a4a00"
            background = "#fff0ee" if forbidden else "#fff8eb"
            esc = html.escape
            pair_name = esc(names[r["a"]]) + ' <span style="color:' + accent + '">＋</span> ' + esc(names[r["b"]])
            st.markdown(
                f'<div style="background:{background};border:1px solid {accent};border-left:6px solid {accent};border-radius:14px;padding:16px;margin:12px 0;color:#263b33;overflow-wrap:anywhere;">'
                f'<div style="font-size:.8rem;font-weight:700;color:{accent};margin-bottom:6px;">{esc(r["level"])}</div>'
                f'<div style="font-size:1.35rem;font-weight:800;line-height:1.5;color:{accent};margin-bottom:10px;">{pair_name}</div>'
                f'<div style="font-size:.9rem;line-height:1.7;white-space:pre-wrap;">등록한 이유: {esc(r["reason"])}</div>'
                f'<div style="font-size:.8rem;color:#536b61;margin-top:6px;">확인 출처: {esc(r["source"])}</div></div>',
                unsafe_allow_html=True,
            )
    if not matched:
        st.info("검색 결과 사이에 등록된 주의 조합이 없습니다. 함께 복용해도 안전하다는 뜻은 아닙니다.")


def symptom_search(items):
    st.markdown("### 🔎 증상으로 약 찾기")
    with st.form("symptom_search"):
        picked = st.multiselect("증상 선택 · 여러 개 선택 가능", SYMPTOMS)
        text = st.text_input("또는 증상 입력", placeholder="예: 두통, 발열", max_chars=160)
        submitted = st.form_submit_button("등록된 약에서 찾기", type="primary", use_container_width=True)
    if submitted:
        emergency = ["호흡곤란", "숨이 안", "숨을 못", "가슴통증", "가슴 통증", "흉통", "의식", "마비", "피를 토", "극심", "갑자기 심한 두통"]
        if any(word in text for word in emergency):
            st.error("응급 증상일 수 있어 약 검색 결과를 표시하지 않습니다. 심한 호흡곤란·흉통·의식저하·갑작스러운 마비 등이 있으면 즉시 119에 연락하세요.")
            return
        requested = sorted(set(picked + [word for word in SYMPTOMS if word in text]))
        if not requested:
            st.info("목록에서 증상을 선택하거나 목록에 있는 증상 이름을 입력하세요. 자유 입력은 나열된 증상 단어만 찾으며 진단하지 않습니다.")
            return
        found = find_label_matches(items, requested)
        if not found:
            st.info("등록한 효능 정보와 일치하면서 사용기한이 지나지 않은 약이 없습니다. 약 카드의 연필 버튼에서 설명서를 확인해 효능을 등록해 주세요.")
        if found:
            st.markdown("#### 증상과 효능이 일치하는 약")
            st.caption("사용기한이 남은 약입니다. 복용 가능 여부를 보장하지 않습니다.")
        for item, matched in found:
            esc = html.escape
            st.markdown(f'<div class="card"><div class="name">{esc(item["name"])}</div><div class="detail">효능 정보 일치: {esc(" · ".join(matched))}<br>📍 {esc(item["location"])}<br>📅 사용기한 {esc(item["expiry"])}</div></div>', unsafe_allow_html=True)
        if found:
            st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
            st.markdown("#### ⚠️ 주의")
            show_pair_alerts(found)
            st.caption("복용 전 약사에게 확인하세요. 타인에게 처방된 약은 복용하지 마세요.")


def save_photo(medicine_id, upload):
    from PIL import Image, ImageOps, UnidentifiedImageError
    if upload.size > 8 * 1024 * 1024:
        raise ValueError("사진은 8MB 이하로 올려 주세요.")
    try:
        image = Image.open(io.BytesIO(upload.getvalue()))
        if image.width * image.height > 25000000:
            raise ValueError("사진 해상도가 너무 큽니다.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1200, 1200))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    except (UnidentifiedImageError, OSError):
        raise ValueError("사진 파일을 읽을 수 없습니다.")
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS medicine_photos (id TEXT PRIMARY KEY, image BLOB NOT NULL)")
        con.execute("INSERT OR REPLACE INTO medicine_photos VALUES (?,?)", (medicine_id, buffer.getvalue()))


def get_photo(medicine_id):
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS medicine_photos (id TEXT PRIMARY KEY, image BLOB NOT NULL)")
        row = con.execute("SELECT image FROM medicine_photos WHERE id=?", (medicine_id,)).fetchone()
    return row[0] if row else None



@st.dialog("💊 약 등록", width="small")
def add_dialog():
    photo = st.file_uploader("약 사진 · 선택 (8MB 이하)", type=["jpg", "jpeg", "png", "webp"])
    if photo is not None:
        st.image(photo, caption="등록할 약 사진", width=200)
    with st.form("add_form"):
        name = st.text_input("약 이름", max_chars=80, key="new_name")
        a, b = st.columns(2)
        location = a.text_input("보관 위치", placeholder="거실 서랍", max_chars=60)
        expiry = b.date_input("사용기한", value=None, min_value=date(2000, 1, 1), max_value=date(2100, 12, 31), key="new_expiry")
        note = st.text_input("보관 메모 · 선택", max_chars=160, placeholder="제품에 적힌 보관 조건 등")
        tags = st.multiselect("설명서에 표시된 효능 · 선택", SYMPTOMS, help="포장·설명서에 명시된 효능만 선택하세요. 약 이름으로 추측하지 마세요.")
        pair_data = pair_fields()
        checked = st.checkbox("약 이름·사용기한을 포장과 대조했습니다", key="new_confirm")
        submitted = st.form_submit_button("등록하기", type="primary", use_container_width=True)
        if submitted:
            if not name.strip() or not location.strip() or not expiry or not checked:
                st.error("이름·위치·사용기한과 확인 체크가 필요합니다.")
            elif not valid_pair_fields(pair_data):
                st.error("주의 대상 약을 선택했다면 출처와 상세 메모를 입력하세요.")
            elif len(records()) >= MAX_ITEMS:
                st.error("최대 200개까지 등록할 수 있습니다.")
            else:
                medicine_id = uuid.uuid4().hex
                if photo is not None:
                    try:
                        save_photo(medicine_id, photo)
                    except ValueError as exc:
                        st.error(str(exc))
                        return
                save(dict(id=medicine_id, name=name.strip(), location=location.strip(), expiry=expiry.isoformat(), note=note.strip()))
                save_tags(medicine_id, tags)
                store_pair_fields(medicine_id, pair_data)
                st.rerun()
    st.caption("사진은 앱 서버에 저장하며 외부 AI로 전송하지 않습니다. 개인정보는 가려 주세요. 서버 초기화 시 사진도 사라질 수 있습니다.")


@st.dialog("✏️ 약 정보 수정", width="small")
def edit_dialog(item):
    existing_photo = get_photo(item["id"])
    if existing_photo:
        st.image(existing_photo, caption="저장된 약 사진", width=240)
    photo = st.file_uploader("사진 추가·교체 (8MB 이하)", type=["jpg", "jpeg", "png", "webp"], key="replace_photo_" + item["id"])
    remove_photo = st.checkbox("저장된 사진 삭제", key="delete_photo_" + item["id"]) if existing_photo else False
    with st.form("edit_form"):
        name = st.text_input("약 이름", value=item["name"], max_chars=80)
        location = st.text_input("보관 위치", value=item["location"], max_chars=60)
        expiry = st.date_input("사용기한", value=date.fromisoformat(item["expiry"]), min_value=date(2000, 1, 1), max_value=date(2100, 12, 31))
        note = st.text_input("보관 메모", value=item["note"], max_chars=160)
        tags = st.multiselect("설명서에 표시된 효능", SYMPTOMS, default=label_tags(item["id"]), help="제품 설명서와 대조한 효능만 선택하세요.")
        pair_data = pair_fields(item["id"])
        if st.form_submit_button("수정 저장", type="primary", use_container_width=True):
            if name.strip() and location.strip() and valid_pair_fields(pair_data):
                if photo is not None:
                    try:
                        save_photo(item["id"], photo)
                    except ValueError as exc:
                        st.error(str(exc))
                        return
                elif remove_photo:
                    with connect() as con:
                        con.execute("DELETE FROM medicine_photos WHERE id=?", (item["id"],))
                save(dict(id=item["id"], name=name.strip(), location=location.strip(), expiry=expiry.isoformat(), note=note.strip()))
                save_tags(item["id"], tags)
                store_pair_fields(item["id"], pair_data)
                st.rerun()
            else:
                st.error("이름·위치를 입력하고, 주의 대상이 있으면 출처와 상세 메모도 입력하세요.")
    st.markdown("**이 약에 등록된 주의 조합**")
    names = {r["id"]: r["name"] for r in records()}
    linked = [r for r in pair_records() if item["id"] in (r["a"], r["b"])]
    if not linked:
        st.caption("등록된 조합이 없습니다. 안전성이 확인된 뜻은 아닙니다.")
    for r in linked:
        other = r["b"] if r["a"] == item["id"] else r["a"]
        st.text(names.get(other, "삭제된 약") + " · " + r["level"] + " · " + r["source"] + "\n" + r["reason"])
        if st.button("이 조합 삭제", key="remove_pair_" + r["a"] + r["b"]):
            with connect() as con:
                con.execute("DELETE FROM medicine_pairs WHERE a=? AND b=?", (r["a"], r["b"]))
            st.rerun()
    confirm = st.checkbox("이 약을 목록에서 삭제합니다")
    if st.button("목록에서 삭제", disabled=not confirm, use_container_width=True):
        with connect() as con:
            con.execute("DELETE FROM medicines WHERE id=?", (item["id"],))
            con.execute("DELETE FROM medicine_photos WHERE id=?", (item["id"],))
            con.execute("DELETE FROM medicine_pairs WHERE a=? OR b=?", (item["id"], item["id"]))
        st.rerun()


@st.dialog("🔗 QR로 가족에게 공유", width="small")
def qr_dialog():
    st.caption("배포된 앱 주소를 입력하면 QR을 만듭니다. QR은 앱으로 연결하며 앱을 설치하지 않습니다.")
    url = st.text_input("Streamlit 배포 주소", value=secret("APP_URL"), placeholder="https://내앱.streamlit.app")
    from urllib.parse import urlparse
    parsed = urlparse(url.strip())
    if url and parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
        image = qrcode.make(url.strip())
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        st.image(buffer.getvalue(), width=190)
        st.download_button("QR 이미지 다운로드", buffer.getvalue(), "family-medicine-qr.png", "image/png", use_container_width=True)
    elif url:
        st.warning("https://로 시작하는 정상적인 배포 주소를 입력하세요.")
    st.warning("이 시제품은 로그인 기능이 없습니다. 공개 앱은 주소를 아는 누구나 열람·수정할 수 있습니다. 민감한 정보를 넣지 말고 QR을 공개 게시하지 마세요.")


@st.dialog("💾 백업·복원 / 안내", width="small")
def backup_dialog():
    st.warning("Cloud의 로컬 저장소는 재시작·재배포 때 사라질 수 있습니다. 중요한 데이터는 JSON으로 백업하세요. 별도 영구 DB가 연결된 제품이 아닌 수행평가용 시제품입니다.")
    payload = json.dumps({"version": 1, "medicines": records()}, ensure_ascii=False, indent=2)
    st.download_button("JSON 백업 다운로드", payload, f"medicine-{date.today()}.json", "application/json", use_container_width=True)
    upload = st.file_uploader("이 앱에서 저장한 JSON 복원", type="json")
    confirmed = st.checkbox("기존 목록에 합칩니다 (같은 ID는 덮어씁니다)")
    if st.button("백업 복원", disabled=not upload or not confirmed, use_container_width=True):
        try:
            if upload.size > 1024 * 1024:
                raise ValueError()
            obj = json.loads(upload.getvalue())
            if obj.get("version") != 1 or not isinstance(obj.get("medicines"), list):
                raise ValueError()
            validated = []
            for item in obj["medicines"]:
                clean = {}
                for key, limit in (("id", 64), ("name", 80), ("location", 60), ("expiry", 10), ("note", 160)):
                    val = item[key]
                    if not isinstance(val, str) or len(val) > limit:
                        raise ValueError()
                    clean[key] = val
                if not clean["id"] or not clean["name"].strip() or not clean["location"].strip():
                    raise ValueError()
                parsed = date.fromisoformat(clean["expiry"])
                if not date(2000, 1, 1) <= parsed <= date(2100, 12, 31):
                    raise ValueError()
                validated.append(clean)
            if len({r["id"] for r in records()} | {r["id"] for r in validated}) > MAX_ITEMS:
                raise ValueError()
            with connect() as con:
                con.executemany("INSERT OR REPLACE INTO medicines VALUES (?,?,?,?,?)", [tuple(r[k] for k in ("id", "name", "location", "expiry", "note")) for r in validated])
            st.rerun()
        except (ValueError, KeyError, TypeError, AttributeError):
            st.error("백업 형식이 올바르지 않거나 등록 한도를 초과했습니다. 데이터는 변경하지 않았습니다.")
    st.caption("사용기한은 포장 표기 기준입니다. 개봉 후 사용 가능 기간·보관 상태에 따라 달라질 수 있습니다. 진단·약 추천·복용량 안내는 제공하지 않습니다. 알림은 앱을 열었을 때 표시되며 백그라운드 푸시는 아닙니다.")


st.markdown("""<style>
.st-key-filter_boxes {margin-top:6px;margin-bottom:18px;}
.st-key-filter_boxes [data-testid="stHorizontalBlock"] {flex-wrap:nowrap!important;gap:8px!important;}
.st-key-filter_boxes [data-testid="stColumn"] {min-width:0!important;flex:1 1 0!important;width:33.333%!important;}
.st-key-filter_boxes button {border-radius:6px!important;min-height:46px!important;padding:8px 4px!important;}
/* 폼 제출 버튼에도 앱의 초록색 테마 적용 */
[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"],
[data-testid="stFormSubmitButton"] button[kind="primary"],
[data-testid="stFormSubmitButton"] button[data-testid="stBaseButton-primaryFormSubmit"] {
background:#087a67!important;color:#fff!important;border:1px solid #087a67!important;}
[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] p,
[data-testid="stFormSubmitButton"] button[kind="primary"] p,
[data-testid="stFormSubmitButton"] button[data-testid="stBaseButton-primaryFormSubmit"] p {color:#fff!important;}
[data-testid="stFormSubmitButton"] button:hover {background:#066650!important;border-color:#066650!important;color:white!important;}
.st-key-medicine_cards [data-testid="stHorizontalBlock"] {flex-wrap:nowrap!important;gap:8px!important;}
.st-key-medicine_cards [data-testid="stColumn"] {min-width:0!important;flex:1 1 0!important;width:33.33%!important;}
.st-key-medicine_cards .stButton {display:flex;justify-content:flex-end;}
.st-key-medicine_cards .stButton button {width:32px!important;min-height:30px!important;padding:2px!important;border:0!important;box-shadow:none!important;}
.st-key-medicine_cards [data-testid="stMarkdownContainer"] {overflow-wrap:anywhere;color:#163a32;}
.medicine-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:8px 0 20px;}
.medicine-tile {min-width:0;background:#fff;border:1px solid #cbded4;border-radius:14px;padding:13px 10px;color:#244b3f;font-size:.8rem;line-height:1.6;overflow-wrap:anywhere;}
.tile-name {font-weight:750;font-size:1rem;color:#163a32;margin-bottom:8px;}
.tile-status {font-weight:700;margin-bottom:8px;}
.tile-note {margin-top:8px;color:#536b61;}
@media(max-width:480px){.medicine-grid {gap:6px;}.medicine-tile {padding:10px 7px;font-size:.72rem;}.tile-name {font-size:.86rem;}}
[data-testid="stHeader"] {height:0;background:transparent;}
[data-testid="stMainBlockContainer"] {max-width:640px;padding:1.15rem 1.15rem .8rem;}
[data-testid="stVerticalBlock"] {gap:.75rem;}
.stApp {background:#f3f8f6;color:#163a32;color-scheme:light;}
.screen-navigation {display:block;text-align:center;padding:12px 16px;margin:8px 0 16px;background:#fff;color:#174f43!important;border:1px solid #afcfc2;border-radius:12px;text-decoration:none!important;font-weight:650;}
.screen-navigation:hover {background:#e4f3ec;}
.app-heading {display:flex;flex-direction:column;gap:9px;margin:0 0 5px;}
.app-heading h1 {font-size:1.65rem!important;line-height:1.35!important;margin:0!important;padding:0!important;letter-spacing:-.035em;color:#163a32;}
.app-heading p {font-size:.8rem;line-height:1.5;margin:0;color:#536b61;}
[data-testid="stCaptionContainer"] p {font-size:.78rem;line-height:1.5;color:#536b61;}
.stButton button,.stDownloadButton button {min-height:2.7rem;border-radius:12px;font-size:.9rem;font-weight:650;background:#fff!important;color:#174f43!important;border:1px solid #afcfc2!important;box-shadow:0 2px 5px #163a3208;}
.stButton button p,.stDownloadButton button p {color:inherit!important;line-height:1.4;}
.stButton button:hover:not(:disabled),.stDownloadButton button:hover {background:#e4f3ec!important;border-color:#408c73!important;}
.stButton button[kind="primary"] {background:#087a63!important;color:#fff!important;border-color:#087a63!important;}
.stButton button[kind="primary"]:hover:not(:disabled) {background:#066650!important;color:#fff!important;}
.stButton button:disabled {background:#e6ece9!important;color:#57675f!important;border-color:#c6d2cb!important;opacity:1!important;box-shadow:none;}
/* 어두운 테마에서도 입력창과 라벨의 대비를 명시합니다. */
[data-testid="stWidgetLabel"] p {color:#163a32!important;}
[data-testid="stMultiSelect"] [data-baseweb="select"] > div,
[data-testid="stTextInput"] [data-baseweb="input"],
[data-testid="stTextInput"] input {background:#fff!important;color:#163a32!important;border-color:#9bbcaf!important;}
[data-testid="stMultiSelect"] input {color:#163a32!important;caret-color:#163a32!important;}
[data-testid="stTextInput"] input::placeholder {color:#62796f!important;opacity:1!important;}
[data-testid="stMultiSelect"] [data-baseweb="tag"] {background:#d8eee4!important;color:#164f3c!important;}
[data-testid="stMultiSelect"] [data-baseweb="tag"] span {color:#164f3c!important;}
[data-testid="stMultiSelect"] svg {fill:#164f3c!important;color:#164f3c!important;}
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="popover"] [role="option"] {background:#fff!important;color:#163a32!important;}
[data-baseweb="popover"] [role="option"]:hover {background:#e4f3ec!important;}
[data-testid="stSelectbox"] {font-size:.9rem;}
[data-testid="stRadio"] [role="radiogroup"] {display:flex;flex-direction:column;flex-wrap:nowrap;gap:.55rem;background:#fff;border:1px solid #b8d1c5;border-radius:12px;padding:9px 12px;}
[data-testid="stRadio"] label p {color:#174f43!important;font-size:.85rem!important;}
[data-baseweb="select"] > div {background:#fff!important;color:#163a32!important;border-color:#b8d1c5!important;}
[data-baseweb="select"] input {color:#163a32!important;}
.stats {display:flex;gap:10px;margin:1px 0 17px;}
.stat {flex:1;background:white;border:1px solid #dfebe6;border-radius:14px;padding:11px 6px;text-align:center;font-size:12px;color:#49665d;}
.stat b {display:block;font-size:23px;color:#086f5a;line-height:1.35;margin-bottom:2px;}
.card {background:white;border:1px solid #dce9e3;border-radius:17px;padding:18px 20px;margin-bottom:18px;box-shadow:0 4px 16px #183e3007;}
.card .name {font-size:20px;font-weight:750;line-height:1.4;overflow-wrap:anywhere;}
.card .detail {font-size:14px;line-height:1.7;overflow-wrap:anywhere;}
.pill {display:inline-block;border-radius:7px;background:#f1f6f3;padding:3px 8px;font-size:12px;margin:7px 0;}
.foot {font-size:11px;color:#536b61;line-height:1.6;margin-top:4px;}
@media(max-width:400px) {
[data-testid="stMainBlockContainer"]{padding:.85rem .85rem .6rem;}
[data-testid="stVerticalBlock"]{gap:.6rem;}
.card{padding:14px 16px;}.stat{padding:8px 5px;}
.app-heading h1{font-size:1.5rem!important;}
}
@media(max-height:700px) {
[data-testid="stMainBlockContainer"]{padding-top:.65rem;}
[data-testid="stVerticalBlock"]{gap:.5rem;}
.stat{padding:6px 5px;}.card{padding:12px 16px;}
}
.tile-name {font-size:1.25rem!important;font-weight:800!important;line-height:1.4!important;margin:4px 0 10px!important;overflow-wrap:anywhere;}

.st-key-medicine_cards [class*="st-key-medicine_heading_"] [data-testid="stHorizontalBlock"] {gap:8px!important;align-items:center!important;}
.st-key-medicine_cards [class*="st-key-medicine_heading_"] [data-testid="stColumn"]:first-child {flex:1 1 0!important;width:auto!important;min-width:0!important;}
.st-key-medicine_cards [class*="st-key-medicine_heading_"] [data-testid="stColumn"]:last-child {flex:0 0 32px!important;width:32px!important;min-width:32px!important;}
.st-key-medicine_cards [class*="st-key-medicine_heading_"] .tile-name {margin:0!important;}
</style>""", unsafe_allow_html=True)

# 첫 방문에는 시작 화면을 표시하고, 이후 상호작용에서는 메인을 유지합니다.
if st.query_params.get("screen") in ("cabinet", "symptoms"):
    st.session_state["entered_app"] = True
    st.session_state["medicine_page"] = st.query_params.get("screen")

if not st.session_state.get("entered_app", False):
    st.markdown("""<style>
.welcome {text-align:center;padding:clamp(28px,9vh,86px) 8px 22px;}
.welcome-logo {position:relative;width:108px;height:108px;margin:0 auto 26px;
  background:linear-gradient(145deg,#129b7b,#066650);border-radius:30px;
  box-shadow:0 12px 30px #087a6326;display:flex;align-items:center;justify-content:center;}
.logo-cross {position:relative;width:52px;height:52px;}
.logo-cross::before,.logo-cross::after {content:"";position:absolute;background:white;border-radius:5px;}
.logo-cross::before {width:18px;height:52px;left:17px;top:0;}
.logo-cross::after {width:52px;height:18px;left:0;top:17px;}
.welcome .welcome-title {font-size:2rem!important;font-weight:750;line-height:1.35;color:#163a32;letter-spacing:-.05em;padding:0!important;margin:0 0 12px;text-align:center!important;}
.welcome .welcome-title a,.welcome .welcome-title [data-testid="stHeaderActionElements"] {display:none!important;}
.welcome .welcome-title {display:block!important;width:100%!important;}
.welcome p {text-align:center!important;}
.welcome p {color:#536b61;font-size:.95rem;line-height:1.8;margin:0;}
.welcome .family {display:inline-block;margin-top:24px;padding:8px 18px;
  background:#e4f3ec;border-radius:30px;color:#17604e;font-size:.82rem;}
</style>
<div class="welcome">
  <div class="welcome-logo" role="img" aria-label="초록 약상자 십자 로고"><div class="logo-cross"></div></div>
  <div class="welcome-title" role="heading" aria-level="1">우리집 약상자</div>
  <p>우리 가족의 약, 한곳에 안전하게<br>AI 기반 가정 상비약 관리</p>
</div>""", unsafe_allow_html=True)
    if st.button("약상자 열기 →", type="primary", use_container_width=True):
        st.session_state["entered_app"] = True
        st.rerun()
    st.stop()

if st.session_state.get("medicine_page", "cabinet") != "symptoms":
    st.markdown('<header class="app-heading"><h1>💊 우리집 약상자</h1></header>', unsafe_allow_html=True)
try:
    items = records()
except sqlite3.Error:
    st.error("저장소에 연결하지 못했습니다. 서버 저장 권한을 확인하세요.")
    st.stop()
# 이동 콜백은 화면 렌더링 전에 실행하여 폼 상태와 무관하게 이동합니다.
def go_to_cabinet():
    st.session_state["medicine_page"] = "cabinet"


def go_to_symptoms():
    st.session_state["medicine_page"] = "symptoms"


page = st.session_state.get("medicine_page", "cabinet")
if page == "symptoms":
    st.markdown('<a class="screen-navigation" href="?screen=cabinet" target="_self">약상자로 돌아가기</a>', unsafe_allow_html=True)
    symptom_search(items)
    st.stop()

expired = sum(status(r["expiry"])[3] < 0 for r in items)
soon = sum(0 <= status(r["expiry"])[3] <= 30 for r in items)
st.markdown(f'<div class="stats"><div class="stat"><b>{len(items)}</b>전체 약</div><div class="stat"><b>{soon}</b>30일 이내</div><div class="stat"><b>{expired}</b>기한 경과</div></div>', unsafe_allow_html=True)
# 가로로 나란한 네모 버튼으로 분류를 선택합니다.
options = ["전체 보기", "30일 이내", "기한 경과"]
if st.session_state.get("filter_choice") not in options:
    st.session_state["filter_choice"] = "전체 보기"

def choose_filter(value):
    st.session_state["filter_choice"] = value

with st.container(key="filter_boxes"):
    tabs = st.columns(3, gap="small")
    for column, option in zip(tabs, options):
        column.button(option, key="filter_box_" + option,
                      type="primary" if st.session_state["filter_choice"] == option else "secondary",
                      use_container_width=True, on_click=choose_filter, args=(option,))
filter_by = st.session_state["filter_choice"]
visible = [r for r in items if filter_by == "전체 보기" or (filter_by == "30일 이내" and 0 <= status(r["expiry"])[3] <= 30) or (filter_by == "기한 경과" and status(r["expiry"])[3] < 0)]
if visible:
    with st.container(key="medicine_cards"):
        for offset in range(0, len(visible), 3):
            columns = st.columns(3, gap="small")
            for column, medicine in zip(columns, visible[offset:offset + 3]):
                with column:
                    with st.container(border=True):
                        esc = html.escape
                        with st.container(key="medicine_heading_" + medicine["id"]):
                            name_column, edit_column = st.columns([4, 1], gap="small", vertical_alignment="center")
                            with name_column:
                                st.markdown(f'<div class="tile-name">{esc(medicine["name"])}</div>', unsafe_allow_html=True)
                            with edit_column:
                                if st.button("✏️", key="edit_" + medicine["id"], help="약 수정"):
                                    edit_dialog(medicine)
                        saved_photo = get_photo(medicine["id"])
                        if saved_photo:
                            st.image(saved_photo, use_container_width=True)
                        _, message, color, _ = status(medicine["expiry"])
                        st.markdown(f'<div class="tile-status" style="color:{color}">{esc(message)}</div><div>📍 {esc(medicine["location"])}</div><div>사용기한<br>{esc(medicine["expiry"])}</div><div class="tile-note">{esc(medicine["note"] or "메모 없음")}</div>', unsafe_allow_html=True)
else:
    st.info("해당하는 약이 없습니다.")
if st.button("＋ 약 등록", type="primary", use_container_width=True):
    for k in ("new_name", "new_expiry", "new_confirm"):
        st.session_state.pop(k, None)
    add_dialog()
if st.button("🔗 QR 공유", use_container_width=True):
    qr_dialog()
st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
st.markdown('<a class="screen-navigation" href="?screen=symptoms" target="_self">증상으로 약 찾기</a>', unsafe_allow_html=True)
st.markdown('<div class="foot">포장 사용기한·보관 조건을 직접 확인하세요. 진단·복약 추천 불가.<br>앱을 열 때 기한 알림 표시 · 서버 임시 저장(재시작 시 소실 가능)</div>', unsafe_allow_html=True)
