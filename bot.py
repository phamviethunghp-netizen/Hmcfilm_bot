# -*- coding: utf-8 -*-
"""
Minciufilm Bot - Local / Railway
Phien ban: 4.0 - Viet lai hoan toan, da kiem tra logic
"""

import os, json, logging, re, urllib.request
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters,
)
import gspread
from google.oauth2.service_account import Credentials

# Doc file .env khi chay local (bo qua neu khong co thu vien)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# 1. CONFIG
# ══════════════════════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
SHEET_ID       = os.environ["SHEET_ID"]
ALLOWED_USERS  = (
    list(map(int, os.environ["ALLOWED_USERS"].split(",")))
    if os.environ.get("ALLOWED_USERS", "").strip() else []
)

# ══════════════════════════════════════════════════════════════════════════════
# 2. AI PROVIDERS
# ══════════════════════════════════════════════════════════════════════════════

# Provider co san - chi can dien key vao .env la dung duoc
BUILTIN = {
    "groq": {
        "name": "Groq - Llama 3.3 70B (mien phi)",
        "key_env": "GROQ_API_KEY",
        "type": "groq",
        "model": "llama-3.3-70b-versatile",
        "url": "https://console.groq.com",
    },
    "gemini": {
        "name": "Google Gemini 2.0 Flash (mien phi)",
        "key_env": "GEMINI_API_KEY",
        "type": "gemini",
        "model": "gemini-2.0-flash",
        "url": "https://aistudio.google.com/apikey",
    },
    "openrouter": {
        "name": "OpenRouter (co model mien phi)",
        "key_env": "OPENROUTER_API_KEY",
        "type": "openai_compat",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "base_url": "https://openrouter.ai/api/v1",
        "url": "https://openrouter.ai/keys",
    },
    "openai": {
        "name": "OpenAI GPT-4o mini",
        "key_env": "OPENAI_API_KEY",
        "type": "openai_compat",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "name": "Anthropic Claude Haiku",
        "key_env": "ANTHROPIC_API_KEY",
        "type": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "url": "https://console.anthropic.com",
    },
    "deepseek": {
        "name": "DeepSeek Chat",
        "key_env": "DEEPSEEK_API_KEY",
        "type": "openai_compat",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "url": "https://platform.deepseek.com/api_keys",
    },
    "mistral": {
        "name": "Mistral AI",
        "key_env": "MISTRAL_API_KEY",
        "type": "openai_compat",
        "model": "mistral-small-latest",
        "base_url": "https://api.mistral.ai/v1",
        "url": "https://console.mistral.ai/api-keys",
    },
}

CUSTOM_FILE = "custom_providers.json"

def load_custom() -> dict:
    try:
        if os.path.exists(CUSTOM_FILE):
            with open(CUSTOM_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_custom(data: dict):
    with open(CUSTOM_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def all_providers() -> dict:
    merged = dict(BUILTIN)
    merged.update(load_custom())
    return merged

def available_providers() -> list:
    result = []
    for pid, cfg in all_providers().items():
        if cfg.get("type") == "openai_compat" and cfg.get("key"):
            result.append(pid)
        elif cfg.get("key_env") and os.environ.get(cfg["key_env"], "").strip():
            result.append(pid)
    return result

# Luu provider dang dung theo user_id
_active: dict = {}

def get_provider(uid: int) -> str | None:
    chosen = _active.get(uid)
    if chosen and chosen in all_providers():
        cfg = all_providers()[chosen]
        has = cfg.get("key") or os.environ.get(cfg.get("key_env", ""), "").strip()
        if has:
            return chosen
    av = available_providers()
    return av[0] if av else None

def set_provider(uid: int, pid: str):
    _active[uid] = pid

# ─── Goi AI ───────────────────────────────────────────────────────────────────
async def call_ai(pid: str, system: str, user_msg: str, max_tokens: int = 2000) -> str:
    cfg   = all_providers()[pid]
    ptype = cfg["type"]
    model = cfg["model"]
    key   = cfg.get("key") or os.environ.get(cfg.get("key_env", ""), "")

    # OpenAI-compatible (OpenAI, OpenRouter, DeepSeek, Mistral, custom...)
    if ptype == "openai_compat":
        base = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
        body = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_msg},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]

    # Groq SDK
    elif ptype == "groq":
        from groq import Groq
        r = Groq(api_key=key).chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
        )
        return r.choices[0].message.content

    # Google Gemini
    elif ptype == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=key)
        return genai.GenerativeModel(model_name=model, system_instruction=system).generate_content(user_msg).text

    # Anthropic
    elif ptype == "anthropic":
        import anthropic
        r = anthropic.Anthropic(api_key=key).messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return r.content[0].text

    raise ValueError(f"Loai provider khong hop le: {ptype}")

# ══════════════════════════════════════════════════════════════════════════════
# 3. SYSTEM PROMPTS
# ══════════════════════════════════════════════════════════════════════════════
PROMPT_CONTENT = """Ban la nguoi viet content cho shop Minciufilm — chuyen may anh film vintage tai Viet Nam.

TONE: Gan gui nhu nguoi ban cung me film, KHONG dung ngon ngu sales sao rong.
Co chieu sau kien thuc — biet diem manh that su cua tung dong may.
Xung "Minci" hoac "Minciufilm". Uu tien cam xuc va storytelling.

DIEM MANH TUNG DONG MAY:
- SLR co: dieu chinh thu cong hoan toan → hoc nhieu anh bai ban
- SLR dien: uu tien khau/toc, tien loi → dung hang ngay, du lich, street
- PnS: nho gon, AF nhanh, lens tot → nguoi moi, aesthetic daily
- Rangefinder: viewfinder quang hoc → street photographer sau lang
- Digital compact: mau sac the he cu, khong the tai tao bang smartphone → vibe y2k

LUON BAO GOM:
- COD toan quoc, khong can coc — nhan hang ung moi tra tien
- 1 cau ve "phu hop voi ai"
- CTA ro rang: inbox / comment keyword / DM

FORMAT OUTPUT — dung --- phan cach, KHONG them gi khac:

📘 FACEBOOK
📷 [Ten may] · [Lens neu co]

[2-3 cau highlight — diem manh that su, co cam xuc]

[1 cau vibe / phu hop voi ai]

✦ Tinh trang: [tinh trang]
✦ Gia: [gia] — COD toan quoc, nhan hang ung moi tra tien

Inbox hoac comment "[tu khoa]" de nhan them anh chi tiet nhe!

📍 Minciufilm | may film · may cu · vintage
#minciufilm #filmcamera #[tenmaykhodau] #35mm #analogphotography #mayanhfilm

---
🧵 THREADS
[Ten may] ve kho roi —

[Highlight 2-3 cau ngan, moi cau xuong dong, tu nhien]

Tinh trang: [tinh trang] | Gia: [gia]
Ship COD toan quoc, khong can coc.

Ai quan tam de lai "gia" ben duoi hoac nhan tin Minciufilm nhe 📷

---
📸 INSTAGRAM
[Ten may] 🎞️

[Highlight 2-3 cau co cam xuc]

[1 cau vibe]

——
✦ Tinh trang: [tinh trang]
✦ Gia: [gia] · COD toan quoc · khong can coc
——
DM hoac comment de biet them chi tiet 📩

.
.
.
#minciufilm #filmcamera #analogphotography #filmisnotdead #35mm #vintagecamera #mayanhfilm #analogvibes

---
🎵 TIKTOK SCRIPT
[HOOK — cau keo chu y manh trong 3 giay dau]

Hom nay Minci co [ten may] —
[Highlight, noi nhu dang ke chuyen, 3-4 cau]
[Vibe / phu hop voi ai]
Tinh trang: [tinh trang]

Gia [gia] — ship COD, khong can coc, comment "gia" hoac nhan Minciufilm!

📌 Text overlay: "[Ten may]" · "Tinh trang: [tinh trang]" · "Gia: [gia]"
#minciufilm #filmcamera #mayanhfilm #filmisnotdead

---
📲 STORY / REELS
Slide 1: [TEN MAY 📷]
Slide 2: [1 cau du manh de dung nguoi xem lai]
Slide 3:
✦ Tinh trang: [tinh trang]
✦ Gia: [gia]
✦ COD toan quoc · khong can coc
→ Nhan tin Minciufilm

Caption Reels: [Ten may] — [highlight dau tien]. Inbox Minciufilm de hoi them nhe 📷
#minciufilm #filmcamera"""

PROMPT_PARSE_ADD = """Trich xuat thong tin may anh tu tin nhan tieng Viet, tra ve JSON thuan (khong markdown).
Keys: ten_may, gia_nhap, tien_sua, gia_ban, ngay_nhap, tinh_trang, ghi_chu
Tien la so nguyen VND: 800k=800000, 1.2tr=1200000, 1tr=1000000. Ngay: DD/MM/YYYY.
Thieu thi de "" hoac 0.
Vi du output: {"ten_may":"Canon AE-1","gia_nhap":800000,"tien_sua":0,"gia_ban":0,"ngay_nhap":"21/05/2026","tinh_trang":"8/10","ghi_chu":""}"""

PROMPT_PARSE_UPDATE = """Trich xuat thong tin CAP NHAT may anh tu tin nhan tieng Viet, tra ve JSON thuan.
Keys cho phep: id (so nguyen, BAT BUOC), ten_may, gia_nhap, tien_sua, gia_ban, tinh_trang, ghi_chu
Chi tra ve key nao duoc de cap trong tin nhan. Bo qua key khong nhac toi.
Tien la so nguyen VND: 300k=300000, 1.8tr=1800000.
Vi du: "ID 3 sua 300k gia ban 1.8tr" → {"id":3,"tien_sua":300000,"gia_ban":1800000}"""

# ══════════════════════════════════════════════════════════════════════════════
# 4. GOOGLE SHEETS
# ══════════════════════════════════════════════════════════════════════════════
HEADERS = ["ID","Ten may","Gia nhap","Tien sua","Gia ban",
           "Ngay nhap","Ngay ban","Tinh trang","Ghi chu","Tien loi"]
SCOPES  = ["https://www.googleapis.com/auth/spreadsheets",
           "https://www.googleapis.com/auth/drive"]

def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if creds_json.strip():
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(creds_json)
            path = f.name
    else:
        path = "credentials.json"
    creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    sh = gspread.authorize(creds).open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet("Kho")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("Kho", rows=1000, cols=len(HEADERS))
        ws.append_row(HEADERS)
    return ws

def fmt(v):
    try:
        return f"{int(v):,}d".replace(",", ".")
    except Exception:
        return str(v)

def parse_money(t: str) -> int:
    t = str(t).strip().lower()
    t = t.replace("trieu", "000000").replace("tr", "000000")
    t = t.replace("k", "000").replace("d", "").replace(".", "").replace(",", "")
    try:
        return int(t)
    except Exception:
        return 0

def is_allowed(uid: int) -> bool:
    return not ALLOWED_USERS or uid in ALLOWED_USERS

# ══════════════════════════════════════════════════════════════════════════════
# 5. MENU CO DINH
# ══════════════════════════════════════════════════════════════════════════════
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📝 Tao Content"),    KeyboardButton("📦 Nhap Kho")],
        [KeyboardButton("📊 Xem Kho"),        KeyboardButton("🔍 Tim Kiem")],
        [KeyboardButton("✏️ Cap Nhat May"),  KeyboardButton("✅ Da Ban")],
        [KeyboardButton("🤖 Doi AI"),          KeyboardButton("📋 Huong Dan")],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Chon chuc nang...",
)

# Conversation states
(
    ST_CONTENT,
    ST_ADD,
    ST_SEARCH,
    ST_SOLD,
    ST_UPDATE,
    ST_AI_NAME,
    ST_AI_URL,
    ST_AI_KEY,
    ST_AI_MODEL,
) = range(9)

# ══════════════════════════════════════════════════════════════════════════════
# 6. HELPER — xay dung text xem kho
# ══════════════════════════════════════════════════════════════════════════════
async def build_stock_text() -> str:
    rows      = get_sheet().get_all_values()
    data      = [r for r in rows[1:] if len(r) > 1 and r[1].strip()]
    con_hang  = [r for r in data if len(r) <= 6 or not r[6].strip()]
    da_ban    = [r for r in data if len(r) > 6 and r[6].strip()]
    tong_von  = sum(parse_money(r[2]) + parse_money(r[3]) for r in da_ban if len(r) > 3)
    tong_ban  = sum(parse_money(r[4]) for r in da_ban if len(r) > 4 and r[4])
    tong_loi  = sum(parse_money(r[9]) for r in da_ban if len(r) > 9 and r[9])

    txt = (
        f"📊 *Kho Minciufilm* — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"📦 Con kho: *{len(con_hang)} may*\n"
        f"✅ Da ban: *{len(da_ban)} may*\n"
    )
    if con_hang:
        txt += "\n── Con hang ──\n"
        for r in con_hang[:12]:
            gia = fmt(r[4]) if len(r) > 4 and r[4] else "Chua dinh gia"
            sua = f" (sua {fmt(r[3])})" if len(r) > 3 and r[3] and r[3] != "0" else ""
            txt += f"• [{r[0]}] {r[1]}{sua} → {gia}\n"
        if len(con_hang) > 12:
            txt += f"_... va {len(con_hang) - 12} may khac_\n"
    if da_ban:
        txt += (
            f"\n── Doanh thu ──\n"
            f"💰 Tong von: {fmt(tong_von)}\n"
            f"💵 Tong thu: {fmt(tong_ban)}\n"
            f"🟢 Tien loi: {fmt(tong_loi)}\n"
        )
    if not data:
        txt += "\n_Kho trong — nhan 📦 Nhap Kho de bat dau._"
    return txt

# ══════════════════════════════════════════════════════════════════════════════
# 7. HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

# ─── /start ───────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.effective_message.reply_text("Ban khong co quyen dung bot nay.")
        return
    pid    = get_provider(uid)
    label  = all_providers()[pid]["name"] if pid else "Chua co AI"
    await update.effective_message.reply_text(
        f"📷 *Minciufilm Bot*\n🤖 AI: *{label}*\n\nMenu hien o ban phim ben duoi:",
        parse_mode="Markdown",
        reply_markup=MAIN_KB,
    )

# ─── Xu ly nut bam menu chinh ─────────────────────────────────────────────────
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip()

    # ── Tao Content ──────────────────────────────────────────────────────────
    if text == "📝 Tao Content":
        await update.message.reply_text(
            "📝 *Tao content*\n\nNhap ten may + thong tin:\n"
            "Vi du: `Canon AE-1 50mm f/1.4 tinh trang 8/10 gia 1.5tr`\n"
            "Hoac chi: `Olympus mju-II`\n\n/huy de quay lai.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ST_CONTENT

    # ── Nhap Kho ─────────────────────────────────────────────────────────────
    elif text == "📦 Nhap Kho":
        await update.message.reply_text(
            "📦 *Nhap kho*\n\nNhap thong tin bang van ban tu nhien:\n"
            "Vi du: `Canon AE-1 nhap 800k ngay nhap 21/05/2026 tinh trang 8/10`\n"
            "(Khong can dien day du, bo sung sau bang Cap Nhat May)\n\n/huy de quay lai.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ST_ADD

    # ── Xem Kho ──────────────────────────────────────────────────────────────
    elif text == "📊 Xem Kho":
        msg = await update.message.reply_text("⏳ Dang tai du lieu kho...")
        try:
            txt = await build_stock_text()
            await msg.edit_text(txt, parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Loi: {e}")
        await update.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)
        return ConversationHandler.END

    # ── Tim Kiem ─────────────────────────────────────────────────────────────
    elif text == "🔍 Tim Kiem":
        await update.message.reply_text(
            "🔍 Nhap ten may can tim:\n\n/huy de quay lai.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ST_SEARCH

    # ── Cap Nhat May ──────────────────────────────────────────────────────────
    elif text == "✏️ Cap Nhat May":
        await update.message.reply_text(
            "✏️ *Cap nhat may trong kho*\n\n"
            "Nhap tu nhien, bot tu hieu:\n"
            "• `ID 3 sua 300k`\n"
            "• `ID 3 gia ban 1.8tr`\n"
            "• `ID 3 sua 300k gia ban 1.8tr tinh trang 9/10`\n"
            "• `ID 3 ghi chu da CLA lens sach`\n\n"
            "Xem ID bang 📊 Xem Kho hoac 🔍 Tim Kiem\n/huy de quay lai.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ST_UPDATE

    # ── Da Ban ────────────────────────────────────────────────────────────────
    elif text == "✅ Da Ban":
        await update.message.reply_text(
            "✅ *Danh dau da ban*\n\n"
            "Nhap ID may va gia ban thuc te (cach nhau khoang trang):\n"
            "Vi du: `3 1200000`\n\n"
            "Neu khong co gia ban thi chi nhap ID: `3`\n"
            "Xem ID bang 📊 Xem Kho\n/huy de quay lai.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ST_SOLD

    # ── Doi AI ────────────────────────────────────────────────────────────────
    elif text == "🤖 Doi AI":
        await show_ai_menu(update.message, update.effective_user.id)
        return ConversationHandler.END

    # ── Huong Dan ─────────────────────────────────────────────────────────────
    elif text == "📋 Huong Dan":
        await update.message.reply_text(
            "📋 *Huong dan su dung*\n\n"
            "📝 *Tao Content* — Tao bai dang cho 5 platform tu dong\n"
            "📦 *Nhap Kho* — Nhap hang moi ve (ten, gia nhap, ngay)\n"
            "✏️ *Cap Nhat May* — Them tien sua / gia ban sau khi nhap kho\n"
            "📊 *Xem Kho* — Xem hang con + thong ke doanh thu\n"
            "🔍 *Tim Kiem* — Tim may theo ten\n"
            "✅ *Da Ban* — Danh dau ban xong + cap nhat gia ban\n"
            "🤖 *Doi AI* — Chuyen doi AI provider\n\n"
            "💡 *Quy trinh chuan:*\n"
            "1 📦 Nhap Kho khi mua ve\n"
            "2 ✏️ Cap Nhat May sau khi sua xong\n"
            "3 📝 Tao Content de dang ban\n"
            "4 ✅ Da Ban khi co nguoi mua\n\n"
            "Quan ly AI: /listai · /delaai [id]",
            parse_mode="Markdown",
            reply_markup=MAIN_KB,
        )
        return ConversationHandler.END

    return ConversationHandler.END

# ─── ST_CONTENT: nhan ten may, goi AI tao content ────────────────────────────
async def on_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    info = update.message.text.strip()
    pid  = get_provider(uid)
    if not pid:
        await update.message.reply_text("Chua co AI nao. Nhan 🤖 Doi AI truoc.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    label = all_providers()[pid]["name"]
    msg   = await update.message.reply_text(f"⏳ Dang tao content bang {label}...")
    try:
        content = await call_ai(pid, PROMPT_CONTENT, f"Tao content ban hang cho: {info}")
        await msg.edit_text("✅ Content xong! Sao chep tung phan:")
        for part in content.split("\n---\n"):
            if part.strip():
                await update.message.reply_text(part.strip())
    except Exception as e:
        logger.error(f"content error: {e}")
        await msg.edit_text(f"❌ Loi: {e}")

    await update.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ─── ST_ADD: nhan thong tin, luu vao Sheet ────────────────────────────────────
async def on_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pid = get_provider(uid)
    if not pid:
        await update.message.reply_text("Chua co AI nao. Nhan 🤖 Doi AI truoc.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    msg = await update.message.reply_text("⏳ Dang phan tich thong tin...")
    try:
        raw  = await call_ai(pid, PROMPT_PARSE_ADD, update.message.text, max_tokens=400)
        raw  = re.sub(r"```json|```", "", raw).strip()
        data = json.loads(raw)

        gia_nhap  = int(data.get("gia_nhap") or 0)
        tien_sua  = int(data.get("tien_sua") or 0)
        gia_ban   = int(data.get("gia_ban")  or 0)
        tien_loi  = (gia_ban - gia_nhap - tien_sua) if gia_ban else 0
        ngay_nhap = data.get("ngay_nhap") or datetime.now().strftime("%d/%m/%Y")

        ws     = get_sheet()
        new_id = len(ws.get_all_values())  # hang tiep theo (co header)
        ws.append_row([
            new_id,
            data.get("ten_may", ""),
            gia_nhap, tien_sua, gia_ban,
            ngay_nhap, "",
            data.get("tinh_trang", ""),
            data.get("ghi_chu", ""),
            tien_loi if gia_ban else "",
        ])

        txt = (
            f"✅ *Da them vao kho!*\n\n"
            f"📷 *{data.get('ten_may', 'N/A')}* (ID: {new_id})\n"
            f"💰 Gia nhap: {fmt(gia_nhap)}\n"
            f"🔧 Tien sua: {fmt(tien_sua) if tien_sua else 'Chua co'}\n"
            f"🏷️ Gia ban: {fmt(gia_ban) if gia_ban else 'Chua co'}\n"
            f"📅 Ngay nhap: {ngay_nhap}\n"
            f"📊 Tinh trang: {data.get('tinh_trang', '')}\n"
        )
        if tien_loi:
            txt += f"💵 Du kien loi: {fmt(tien_loi)}\n"

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📝 Tao content luon", callback_data=f"gen_{new_id}"),
        ]])
        await msg.edit_text(txt, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        logger.error(f"add error: {e}")
        await msg.edit_text(f"❌ Loi khi luu kho: {e}")

    await update.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ─── ST_SEARCH: tim kiem trong Sheet ─────────────────────────────────────────
async def on_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kw = update.message.text.strip().lower()
    try:
        rows    = get_sheet().get_all_values()
        results = [r for r in rows[1:] if len(r) > 1 and kw in r[1].lower()]
        if not results:
            await update.message.reply_text(f"Khong tim thay may nao voi tu khoa: *{kw}*", parse_mode="Markdown")
        else:
            txt = f"🔍 *{len(results)} ket qua*:\n\n"
            for r in results[:10]:
                status = "✅ Con" if (len(r) <= 6 or not r[6].strip()) else "🔴 Da ban"
                gia    = fmt(r[4]) if len(r) > 4 and r[4] else "N/A"
                sua    = f" | Sua: {fmt(r[3])}" if len(r) > 3 and r[3] and r[3] != "0" else ""
                txt   += f"*[{r[0]}] {r[1]}*\n   {status} | Nhap: {fmt(r[2])}{sua} | Ban: {gia}\n\n"
            await update.message.reply_text(txt, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Loi: {e}")

    await update.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ─── ST_UPDATE: cap nhat thong tin may ───────────────────────────────────────
FIELD_COL = {"ten_may": 2, "gia_nhap": 3, "tien_sua": 4, "gia_ban": 5,
             "tinh_trang": 8, "ghi_chu": 9}
FIELD_LABEL = {"ten_may": "Ten may", "gia_nhap": "Gia nhap", "tien_sua": "Tien sua",
               "gia_ban": "Gia ban", "tinh_trang": "Tinh trang", "ghi_chu": "Ghi chu"}

async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pid = get_provider(uid)
    if not pid:
        await update.message.reply_text("Chua co AI nao.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    msg = await update.message.reply_text("⏳ Dang xu ly...")
    try:
        raw  = await call_ai(pid, PROMPT_PARSE_UPDATE, update.message.text, max_tokens=300)
        raw  = re.sub(r"```json|```", "", raw).strip()
        data = json.loads(raw)

        item_id = data.get("id")
        if not item_id:
            await msg.edit_text(
                "❌ Khong tim thay ID may.\nNhap lai dung dang: `ID 3 sua 300k gia ban 1.8tr`",
                parse_mode="Markdown",
            )
            await update.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)
            return ConversationHandler.END

        ws       = get_sheet()
        all_rows = ws.get_all_values()
        row_idx  = next(
            (i + 1 for i, r in enumerate(all_rows) if r and str(r[0]) == str(item_id)),
            None,
        )
        if not row_idx:
            await msg.edit_text(f"❌ Khong tim thay may ID {item_id} trong kho.")
            await update.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)
            return ConversationHandler.END

        ten_may = all_rows[row_idx - 1][1] if len(all_rows[row_idx - 1]) > 1 else f"ID {item_id}"
        updated = []
        for field, col in FIELD_COL.items():
            if field in data:
                ws.update_cell(row_idx, col, data[field])
                val = fmt(data[field]) if field in ("gia_nhap", "tien_sua", "gia_ban") else str(data[field])
                updated.append(f"• {FIELD_LABEL[field]}: {val}")

        # Tinh lai tien loi neu co du lieu
        refreshed = ws.row_values(row_idx)
        gn = parse_money(refreshed[2]) if len(refreshed) > 2 else 0
        ts = parse_money(refreshed[3]) if len(refreshed) > 3 else 0
        gb = parse_money(refreshed[4]) if len(refreshed) > 4 else 0
        if gn and gb:
            loi = gb - gn - ts
            ws.update_cell(row_idx, 10, loi)
            updated.append(f"• Tien loi (tu tinh): {fmt(loi)}")

        if updated:
            txt = f"✅ *Da cap nhat [{item_id}] {ten_may}*\n\n" + "\n".join(updated)
        else:
            txt = "Khong co truong nao duoc cap nhat. Thu lai voi cu phap ro hon."

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📝 Tao content", callback_data=f"gen_{item_id}"),
            InlineKeyboardButton("📊 Xem kho",     callback_data="view_stock"),
        ]])
        await msg.edit_text(txt, parse_mode="Markdown", reply_markup=kb)
    except json.JSONDecodeError:
        await msg.edit_text(
            "❌ Khong hieu thong tin. Thu lai:\n`ID 3 sua 300k gia ban 1.8tr`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"update error: {e}")
        await msg.edit_text(f"❌ Loi: {e}")

    await update.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ─── ST_SOLD: danh dau ban ────────────────────────────────────────────────────
async def on_sold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split()
    try:
        item_id      = int(parts[0])
        gia_ban_thuc = parse_money(parts[1]) if len(parts) > 1 else 0

        ws       = get_sheet()
        all_rows = ws.get_all_values()
        row_idx  = next(
            (i + 1 for i, r in enumerate(all_rows) if r and str(r[0]) == str(item_id)),
            None,
        )
        if not row_idx:
            await update.message.reply_text(f"❌ Khong tim thay may ID {item_id}")
        else:
            ngay_ban = datetime.now().strftime("%d/%m/%Y")
            ws.update_cell(row_idx, 7, ngay_ban)

            if gia_ban_thuc:
                gn   = parse_money(str(all_rows[row_idx - 1][2])) if len(all_rows[row_idx - 1]) > 2 else 0
                ts   = parse_money(str(all_rows[row_idx - 1][3])) if len(all_rows[row_idx - 1]) > 3 else 0
                loi  = gia_ban_thuc - gn - ts
                ws.update_cell(row_idx, 5,  gia_ban_thuc)
                ws.update_cell(row_idx, 10, loi)
                await update.message.reply_text(
                    f"✅ *Da ban!*\n\n"
                    f"📷 {all_rows[row_idx - 1][1]}\n"
                    f"📅 {ngay_ban} | 💰 {fmt(gia_ban_thuc)} | 💵 Loi: {fmt(loi)}",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    f"✅ *{all_rows[row_idx - 1][1]}* da duoc danh dau ban vao {ngay_ban}.",
                    parse_mode="Markdown",
                )
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Nhap sai dinh dang.\nDung: `ID gia_ban`\nVi du: `3 1200000`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Loi: {e}")

    await update.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ─── Huy thao tac ─────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Huy. Quay lai menu chinh.", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════════════════════════
# 8. AI MENU & CUSTOM PROVIDER
# ══════════════════════════════════════════════════════════════════════════════
async def show_ai_menu(message, uid: int):
    av      = available_providers()
    current = get_provider(uid)
    rows    = []
    for pid, cfg in all_providers().items():
        has = pid in av
        act = pid == current
        icon  = "✅" if act else ("🟢" if has else "🔴")
        extra = " (dang dung)" if act else ("" if has else " — can key")
        rows.append([InlineKeyboardButton(
            f"{icon} {cfg['name']}{extra}", callback_data=f"setai_{pid}"
        )])
    rows.append([InlineKeyboardButton("➕ Them AI moi", callback_data="addai_start")])
    custom = load_custom()
    if custom:
        rows.append([InlineKeyboardButton("🗑️ Xoa AI custom", callback_data="delaai_menu")])
    await message.reply_text(
        "🤖 *Chon AI provider*\n\n"
        "🟢 Co key · 🔴 Chua co · ✅ Dang dung\n\n"
        "Mien phi: Groq (console.groq.com) · Gemini (aistudio.google.com/apikey)",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )

async def cmd_listai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    av  = available_providers()
    cur = get_provider(update.effective_user.id)
    txt = "🤖 *Danh sach AI providers*\n\n"
    for pid, cfg in all_providers().items():
        icon  = "✅" if pid == cur else ("🟢" if pid in av else "🔴")
        ctype = "custom" if pid in load_custom() else "built-in"
        txt  += f"{icon} *{cfg['name']}*\n   ID: `{pid}` · {ctype}\n\n"
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=MAIN_KB)

async def cmd_delaai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    custom = load_custom()
    if not context.args:
        if not custom:
            await update.message.reply_text("Khong co AI custom nao.", reply_markup=MAIN_KB)
            return
        txt = "AI custom hien co:\n"
        for pid, cfg in custom.items():
            txt += f"• `{pid}` — {cfg['name']}\n"
        txt += "\nDung: /delaai [id]"
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=MAIN_KB)
        return
    pid = context.args[0]
    if pid not in custom:
        await update.message.reply_text(f"Khong tim thay: `{pid}`", parse_mode="Markdown")
        return
    name = custom[pid]["name"]
    del custom[pid]
    save_custom(custom)
    await update.message.reply_text(f"🗑️ Da xoa: *{name}*", parse_mode="Markdown", reply_markup=MAIN_KB)

# Conversation them AI moi
async def addai_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source = update.message or update.callback_query.message
    await source.reply_text(
        "➕ *Them AI provider moi*\n\nNhap *ten hien thi*:\nVi du: `Together AI`\n\n/huy de huy.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ST_AI_NAME

async def addai_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ai_name"] = update.message.text.strip()
    await update.message.reply_text(
        "🌐 Nhap *Base URL*:\nVi du:\n"
        "• Together AI: `https://api.together.xyz/v1`\n"
        "• xAI Grok: `https://api.x.ai/v1`\n"
        "• Ollama local: `http://localhost:11434/v1`",
        parse_mode="Markdown",
    )
    return ST_AI_URL

async def addai_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ai_url"] = update.message.text.strip().rstrip("/")
    await update.message.reply_text(
        "🔑 Nhap *API Key*:\n(Neu khong can key thi nhap: `none`)",
        parse_mode="Markdown",
    )
    return ST_AI_KEY

async def addai_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ai_key"] = update.message.text.strip()
    await update.message.reply_text(
        "🧠 Nhap *ten model*:\nVi du: `meta-llama/Llama-3-70b-chat-hf`",
        parse_mode="Markdown",
    )
    return ST_AI_MODEL

async def addai_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud    = context.user_data
    name  = ud.get("ai_name", "Custom AI")
    url   = ud.get("ai_url", "")
    key   = ud.get("ai_key", "none")
    model = update.message.text.strip()
    pid   = re.sub(r"[^a-z0-9]", "_", name.lower())[:20].strip("_") or "custom"
    custom = load_custom()
    custom[pid] = {"name": name, "type": "openai_compat", "base_url": url,
                   "key": key, "model": model}
    save_custom(custom)
    await update.message.reply_text(
        f"✅ *Da them: {name}*\nID: `{pid}`\nDung 🤖 Doi AI de chon.",
        parse_mode="Markdown",
        reply_markup=MAIN_KB,
    )
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════════════════════════
# 9. CALLBACK QUERY (inline buttons)
# ══════════════════════════════════════════════════════════════════════════════
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data
    uid   = update.effective_user.id

    # Chon AI provider
    if data.startswith("setai_"):
        pid = data[6:]
        if pid not in all_providers():
            await query.message.reply_text("Provider khong ton tai.")
            return
        if pid not in available_providers():
            cfg = all_providers()[pid]
            url = cfg.get("url", "")
            await query.message.reply_text(
                f"❌ *{cfg['name']}* chua co key.\n"
                f"{'Lay key tai: ' + url if url else 'Them key vao .env: ' + cfg.get('key_env','')}",
                parse_mode="Markdown",
            )
            return
        set_provider(uid, pid)
        await query.message.reply_text(
            f"✅ Da chuyen sang *{all_providers()[pid]['name']}*!",
            parse_mode="Markdown",
            reply_markup=MAIN_KB,
        )

    # Bat dau them AI moi
    elif data == "addai_start":
        await addai_entry(update, context)

    # Menu xoa AI custom
    elif data == "delaai_menu":
        custom = load_custom()
        if not custom:
            await query.message.reply_text("Khong co AI custom nao.")
            return
        kb = [[InlineKeyboardButton(f"🗑️ {cfg['name']}", callback_data=f"delaai_{pid}")]
              for pid, cfg in custom.items()]
        await query.message.reply_text("Chon AI can xoa:", reply_markup=InlineKeyboardMarkup(kb))

    # Xoa AI custom cu the
    elif data.startswith("delaai_"):
        pid    = data[7:]
        custom = load_custom()
        if pid in custom:
            name = custom[pid]["name"]
            del custom[pid]
            save_custom(custom)
            await query.message.reply_text(f"🗑️ Da xoa: *{name}*", parse_mode="Markdown",
                                           reply_markup=MAIN_KB)

    # Xem kho (tu inline button)
    elif data == "view_stock":
        try:
            txt = await build_stock_text()
            await query.message.reply_text(txt, parse_mode="Markdown", reply_markup=MAIN_KB)
        except Exception as e:
            await query.message.reply_text(f"❌ Loi: {e}", reply_markup=MAIN_KB)

    # Tao content tu kho (sau khi nhap kho hoac cap nhat)
    elif data.startswith("gen_"):
        item_id = data[4:]
        pid     = get_provider(uid)
        if not pid:
            await query.message.reply_text("Chua co AI nao. Nhan 🤖 Doi AI truoc.")
            return
        label = all_providers()[pid]["name"]
        await query.message.reply_text(f"⏳ Dang tao content bang {label}...")
        try:
            rows = get_sheet().get_all_values()
            row  = next((r for r in rows if r and str(r[0]) == str(item_id)), None)
            if row:
                info    = f"{row[1]}, tinh trang {row[7] if len(row)>7 else ''}, gia {fmt(row[4]) if len(row)>4 else ''}"
                content = await call_ai(pid, PROMPT_CONTENT, f"Tao content ban hang cho: {info}")
                for part in content.split("\n---\n"):
                    if part.strip():
                        await query.message.reply_text(part.strip())
            else:
                await query.message.reply_text(f"Khong tim thay may ID {item_id}.")
        except Exception as e:
            await query.message.reply_text(f"❌ Loi: {e}")
        await query.message.reply_text("Chon chuc nang tiep theo:", reply_markup=MAIN_KB)

# ══════════════════════════════════════════════════════════════════════════════
# 10. MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ConversationHandler xu ly menu + cac luong nhap lieu
    menu_pattern = (
        r"^(📝 Tao Content|📦 Nhap Kho|📊 Xem Kho|🔍 Tim Kiem"
        r"|✏️ Cap Nhat May|✅ Da Ban|🤖 Doi AI|📋 Huong Dan)$"
    )
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(menu_pattern), handle_menu),
            CallbackQueryHandler(addai_entry, pattern="^addai_start$"),
        ],
        states={
            ST_CONTENT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, on_content)],
            ST_ADD:      [MessageHandler(filters.TEXT & ~filters.COMMAND, on_add)],
            ST_SEARCH:   [MessageHandler(filters.TEXT & ~filters.COMMAND, on_search)],
            ST_UPDATE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, on_update)],
            ST_SOLD:     [MessageHandler(filters.TEXT & ~filters.COMMAND, on_sold)],
            ST_AI_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, addai_name)],
            ST_AI_URL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, addai_url)],
            ST_AI_KEY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, addai_key)],
            ST_AI_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, addai_model)],
        },
        fallbacks=[
            CommandHandler("huy",   cmd_cancel),
            CommandHandler("start", cmd_start),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("listai", cmd_listai))
    app.add_handler(CommandHandler("delaai", cmd_delaai))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info(f"Bot khoi dong. Provider co san: {available_providers() or ['(chua co key)']}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
