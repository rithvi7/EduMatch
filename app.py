"""EduMatch: sign in with a User ID, then open a personal dashboard."""

import base64
import hashlib
import html
import inspect
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "school_data.json")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SUBJECTS = [
    "Tamil",
    "English",
    "Hindi",
    "Economics",
    "Accountancy",
    "Commerce",
    "Computer Application",
    "Social Science",
    "Physics",
    "Chemistry",
    "Mathematics",
    "Biology",
]

STUDENT_SOLVE_POINTS = 10
TEACHER_VERIFY_POINTS = 15
TEACHER_SOLVE_POINTS = 25
DAILY_DOUBT_LIMIT = 6
OVERDUE_MINUTES = 45
TEACHER_APPROVALS_NEEDED = 2
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".webm", ".aac", ".flac")

USERNAME_RE = re.compile(r"^(?=(?:[a-z]*[^a-z0-9][a-z]*)+)(?!(?:\D*\d){3})[a-z0-9._-]+$")
USERNAME_HINT = "Use lowercase letters, at least one symbol such as _ . or -, and at most two digits."

LEVELS = (
    (0, "Rookie", "🌱"),
    (10, "Intermediate", "🔥"),
    (25, "Advanced", "⚡"),
    (50, "Expert", "🏆"),
)

BADGE_EMOJI = {
    "Contributor": "🤝",
    "Master Mentor": "🎓",
}


def default_data():
    return {"users": [], "doubts": [], "point_events": []}


def load_data():
    if not os.path.exists(DATA_PATH):
        data = default_data()
        save_data(data)
        return data
    with open(DATA_PATH, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    ensure_teacher_bootstrap(data)
    fan_out_overdue_notices(data)
    return data


def save_data(data):
    with open(DATA_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def next_id(items, prefix):
    highest = 0
    for item in items:
        raw = str(item.get("id", "")).removeprefix(prefix)
        if raw.isdigit():
            highest = max(highest, int(raw))
    return f"{prefix}{highest + 1}"


def now_stamp():
    return datetime.now().replace(microsecond=0).isoformat()


def parse_stamp(value):
    return datetime.fromisoformat(value)


def week_start(moment):
    monday = moment.date() - timedelta(days=moment.weekday())
    return datetime.combine(monday, datetime.min.time())


def find_user(data, user_id):
    for user in data["users"]:
        if user["id"] == user_id:
            return user
    return None


def username_of(user):
    return user.get("username") or user.get("name", "")


def display_name(user):
    return username_of(user)


def find_user_by_username(data, username):
    key = username.strip()
    if not key:
        return None
    for user in data["users"]:
        if username_of(user) == key:
            return user
    return None


def valid_username(username):
    return bool(USERNAME_RE.fullmatch(username.strip()))


def hash_passkey(passkey, salt):
    digest = hashlib.pbkdf2_hmac("sha256", passkey.encode("utf-8"), bytes.fromhex(salt), 120_000)
    return digest.hex()


def new_salt():
    return secrets.token_hex(16)


def set_passkey(user, passkey):
    salt = new_salt()
    user["passkey_salt"] = salt
    user["passkey_hash"] = hash_passkey(passkey, salt)


def passkey_matches(user, passkey):
    salt = user.get("passkey_salt", "")
    expected = user.get("passkey_hash", "")
    if not salt or not expected or not valid_passkey(passkey):
        return False
    actual = hash_passkey(passkey.strip(), salt)
    return secrets.compare_digest(actual, expected)


def valid_passkey(passkey):
    text = passkey.strip()
    return len(text) == 4 and text.isdigit()


def award_points(data, user, points, reason, badge=None):
    user["points"] += points
    if badge and badge not in user["badges"]:
        user["badges"].append(badge)
    data.setdefault("point_events", []).append(
        {
            "user_id": user["id"],
            "points": points,
            "reason": reason,
            "at": now_stamp(),
        }
    )


def weekly_points(data, user_id):
    start = week_start(datetime.now())
    total = 0
    for event in data.get("point_events", []):
        if event["user_id"] == user_id and parse_stamp(event["at"]) >= start:
            total += event["points"]
    return total


def ranked_users(data):
    return sorted(data["users"], key=lambda user: (-user["points"], username_of(user)))


def weekly_wizard(data):
    ranked = sorted(
        data["users"],
        key=lambda user: (-weekly_points(data, user["id"]), -user["points"], username_of(user)),
    )
    if not ranked:
        return None
    leader = ranked[0]
    if weekly_points(data, leader["id"]) <= 0 and leader["points"] <= 0:
        return None
    return leader


def level_for(points):
    current = LEVELS[0]
    for band in LEVELS:
        if points >= band[0]:
            current = band
    return current


def next_level(points):
    for band in LEVELS:
        if points < band[0]:
            return band
    return None


def stretch_kwargs(widget):
    parameters = inspect.signature(widget).parameters
    if "width" in parameters:
        return {"width": "stretch"}
    return {"use_container_width": True}


def flash(message):
    st.session_state["flash"] = message


def show_flash():
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message)


def go(screen, doubt_id=None):
    current = st.session_state.get("screen", "home")
    history = st.session_state.setdefault("history", [])
    if current and current != screen:
        history.append({"screen": current, "doubt_id": st.session_state.get("open_doubt_id")})
    st.session_state["screen"] = screen
    if doubt_id:
        st.session_state["open_doubt_id"] = doubt_id
    elif screen != "doubt":
        st.session_state.pop("open_doubt_id", None)
    st.rerun()


def go_back():
    history = st.session_state.setdefault("history", [])
    if history:
        previous = history.pop()
        st.session_state["screen"] = previous.get("screen", "home")
        if previous.get("doubt_id"):
            st.session_state["open_doubt_id"] = previous["doubt_id"]
        else:
            st.session_state.pop("open_doubt_id", None)
    else:
        st.session_state["screen"] = "home"
    st.rerun()


def sign_out():
    st.session_state.pop("user_id", None)
    st.session_state["screen"] = "home"
    st.session_state["auth_screen"] = "welcome"
    st.session_state["history"] = []
    st.rerun()


def teacher_subjects(user):
    if not user:
        return []
    return [subject for subject in user.get("subjects", []) if subject]


def subject_badges(user):
    subjects = teacher_subjects(user)
    if not subjects:
        return ""
    chips = "".join(f'<span class="subject-chip">{html.escape(subject)}</span>' for subject in subjects)
    return f'<span class="subject-wrap">{chips}</span>'


def role_badge(role, compact=False, user=None):
    if role == "teacher":
        label = "Teacher"
        css = "role-teacher"
        emoji = "🎓"
    else:
        label = "Student"
        css = "role-student"
        emoji = "🎒"
    text = label if compact else f"{emoji} {label}"
    extra = subject_badges(user) if user and role == "teacher" else ""
    return f'<span class="role-badge {css}">{text}</span>{extra}'


def user_chip(user, extra=""):
    return (
        f'<span class="user-chip">{html.escape(username_of(user))} {role_badge(user["role"], user=user)}{extra}</span>'
    )


def subject_checkboxes(prefix, selected=None):
    selected = set(selected or [])
    picked = []
    cols = st.columns(2)
    for index, subject in enumerate(SUBJECTS):
        with cols[index % 2]:
            if st.checkbox(subject, value=subject in selected, key=f"{prefix}-{subject}"):
                picked.append(subject)
    return picked


def is_verified_teacher(user):
    return bool(user) and user.get("role") == "teacher" and user.get("status", "verified") == "verified"


def is_pending_teacher(user):
    return bool(user) and user.get("role") == "teacher" and user.get("status") == "pending"


def verified_teachers(data):
    return [person for person in data["users"] if is_verified_teacher(person)]


def pending_teacher_accounts(data):
    return [person for person in data["users"] if is_pending_teacher(person)]


def ensure_teacher_bootstrap(data):
    changed = False
    teachers = [person for person in data.get("users", []) if person.get("role") == "teacher"]
    verified = [person for person in teachers if person.get("status") == "verified"]
    if len(verified) < 2:
        for person in teachers[:2]:
            if person.get("status") != "verified":
                person["status"] = "verified"
                person.setdefault("approvals", [])
                changed = True
    if changed:
        save_data(data)


def doubts_posted_today(data, user_id):
    today = datetime.now().date()
    count = 0
    for doubt in data.get("doubts", []):
        if doubt.get("student_id") != user_id:
            continue
        try:
            if parse_stamp(doubt["created_at"]).date() == today:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


def is_overdue(doubt):
    if doubt.get("status") != "Open" or doubt.get("answers"):
        return False
    try:
        created = parse_stamp(doubt["created_at"])
    except (TypeError, ValueError):
        return False
    return datetime.now() - created > timedelta(minutes=OVERDUE_MINUTES)


def approve_teacher(data, candidate, reviewer):
    approvals = candidate.setdefault("approvals", [])
    if reviewer["id"] in approvals:
        return "already"
    approvals.append(reviewer["id"])
    if len(approvals) >= TEACHER_APPROVALS_NEEDED:
        candidate["status"] = "verified"
        return "verified"
    return "voted"


def can_see_doubt(user, doubt):
    if not doubt.get("private"):
        return True
    if doubt.get("student_id") == user["id"]:
        return True
    if user.get("role") != "teacher":
        return False
    subjects = teacher_subjects(user)
    if not subjects:
        return True
    return doubt.get("subject") in subjects


def visible_doubts(data, user):
    return [doubt for doubt in data["doubts"] if can_see_doubt(user, doubt)]


def teachers_for_doubt(data, doubt):
    subject = doubt.get("subject")
    matched = [teacher for teacher in verified_teachers(data) if subject in teacher_subjects(teacher)]
    if matched:
        return matched
    return verified_teachers(data)


def notify_teachers_for_doubt(data, doubt, kind, message):
    changed = False
    key = f"{kind}-{doubt.get('id')}"
    for teacher in teachers_for_doubt(data, doubt):
        notices = teacher.setdefault("notifications", [])
        if any(item.get("key") == key for item in notices):
            continue
        notices.append(
            {
                "id": f"n{uuid4().hex[:8]}",
                "key": key,
                "kind": kind,
                "doubt_id": doubt.get("id"),
                "subject": doubt.get("subject"),
                "message": message,
                "at": now_stamp(),
                "read": False,
            }
        )
        changed = True
    return changed


def fan_out_overdue_notices(data):
    changed = False
    for doubt in data.get("doubts", []):
        if not is_overdue(doubt):
            continue
        if notify_teachers_for_doubt(
            data,
            doubt,
            "overdue",
            f"{doubt.get('subject')} doubt has been waiting more than {OVERDUE_MINUTES} minutes.",
        ):
            changed = True
    if changed:
        save_data(data)


def unread_teacher_notices(user):
    return [item for item in user.get("notifications", []) if not item.get("read")]


def ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def attachment_kind(name, mime=""):
    lower = (name or "").lower()
    media = (mime or "").lower()
    if media.startswith("image/") or lower.endswith(IMAGE_EXTS):
        return "image"
    if media.startswith("audio/") or lower.endswith(AUDIO_EXTS):
        return "audio"
    return "file"


def save_upload(upload, prefix):
    ensure_upload_dir()
    name = Path(getattr(upload, "name", "capture.bin")).name
    mime = getattr(upload, "type", "") or ""
    kind = attachment_kind(name, mime)
    if not name or name == "capture.bin":
        if kind == "image":
            name = "photo.jpg"
        elif kind == "audio":
            name = "voice.wav"
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", name) or "file.bin"
    stored = f"{prefix}_{uuid4().hex[:8]}_{safe}"
    path = os.path.join(UPLOAD_DIR, stored)
    raw = upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
    with open(path, "wb") as handle:
        handle.write(raw)
    return {
        "id": f"f{uuid4().hex[:8]}",
        "name": name,
        "path": stored,
        "kind": kind,
        "mime": mime,
        "data": base64.b64encode(raw).decode("ascii"),
    }


def attachment_bytes(item):
    encoded = item.get("data")
    if encoded:
        try:
            return base64.b64decode(encoded)
        except (TypeError, ValueError):
            pass
    path = os.path.join(UPLOAD_DIR, item.get("path", ""))
    if os.path.exists(path):
        with open(path, "rb") as handle:
            return handle.read()
    return None


def render_attachment_list(attachments):
    if not attachments:
        return
    for item in attachments:
        raw = attachment_bytes(item)
        kind = item.get("kind") or attachment_kind(item.get("name", ""), item.get("mime", ""))
        label = item.get("name", "Attachment")
        if kind == "image" and raw:
            st.image(raw, caption=label, width=280)
        elif kind == "audio" and raw:
            st.caption(f"🎤 {label}")
            st.audio(raw)
        elif raw:
            st.caption(f"📎 {label}")
            st.download_button(
                f"⬇️ Download {label}",
                data=raw,
                file_name=label,
                key=f"dl-{item.get('id', label)}",
            )
        else:
            st.caption(f"📎 {label}")


def maybe_audio_input(key):
    recorder = getattr(st, "audio_input", None)
    if recorder is None:
        return None
    return recorder("🎤 Record a voice note", key=key)


def inject_css():
    st.markdown(
        """
        <style>
            [data-testid="stSidebar"] { display: none; }
            header[data-testid="stHeader"],
            [data-testid="stToolbar"],
            [data-testid="stDecoration"],
            [data-testid="stStatusWidget"],
            .stAppDeployButton,
            .stDeployButton,
            #MainMenu { display: none !important; visibility: hidden; }
            .stApp { background: radial-gradient(circle at top left, #ffe7c2 0%, #fff6e8 42%, #e8fff6 100%); }
            .block-container { padding-top: 1.6rem; max-width: 760px; }
            .splash {
                text-align: center;
                padding: 2.4rem 1rem 1.2rem;
            }
            .splash img { width: 148px; height: auto; border-radius: 28px; }
            .splash [data-testid="stImage"] button { display: none !important; }
            .splash h1 {
                margin: 0.85rem 0 0.25rem;
                font-size: 2.6rem;
                font-weight: 900;
                color: #171f2a;
            }
            .splash p {
                margin: 0 auto 1.4rem;
                max-width: 26rem;
                font-weight: 600;
                color: #5c5146;
            }
            .stButton > button, .stFormSubmitButton > button, button[kind] {
                min-height: 3.1rem;
                border-radius: 999px !important;
                font-weight: 800 !important;
                font-size: 1.02rem !important;
                letter-spacing: 0.01em;
                box-shadow: 0 6px 0 rgba(28, 36, 48, 0.12), 0 10px 18px rgba(255, 90, 54, 0.12);
                transition: transform 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease;
            }
            .stButton > button:hover, .stFormSubmitButton > button:hover {
                transform: translateY(-3px) scale(1.03);
                filter: brightness(1.05);
                box-shadow: 0 10px 0 rgba(28, 36, 48, 0.1), 0 16px 22px rgba(255, 90, 54, 0.18);
            }
            .stButton > button:active, .stFormSubmitButton > button:active {
                transform: translateY(2px) scale(0.99);
                box-shadow: 0 2px 0 rgba(28, 36, 48, 0.12);
            }
            .action-slot .stButton > button { color: #fff !important; border: 0 !important; }
            .action-ask .stButton > button { background: linear-gradient(135deg, #ff5a36, #ff8a4c) !important; }
            .action-help .stButton > button { background: linear-gradient(135deg, #1a73e8, #4c8dff) !important; }
            .action-scores .stButton > button { background: linear-gradient(135deg, #12a06a, #3ddc97) !important; }
            .action-check .stButton > button { background: linear-gradient(135deg, #7b5cff, #9b87ff) !important; }
            .action-out .stButton > button { background: linear-gradient(135deg, #c2410c, #ea580c) !important; }
            .action-approve .stButton > button { background: linear-gradient(135deg, #12a06a, #22c55e) !important; color: #fff !important; }
            .action-reject .stButton > button { background: linear-gradient(135deg, #b91c1c, #ef4444) !important; color: #fff !important; }
            .overdue-banner {
                background: linear-gradient(90deg, #fff1c2, #ffd28a);
                border: 2px solid #f4b400;
                border-radius: 16px;
                padding: 0.85rem 1rem;
                margin: 0.4rem 0 0.9rem;
                font-weight: 700;
                color: #7a4a00;
            }
            .pending-banner {
                background: #fff4e5;
                border: 2px solid #ffb347;
                border-radius: 16px;
                padding: 0.85rem 1rem;
                margin: 0.4rem 0 0.9rem;
                font-weight: 700;
                color: #9a4100;
            }
            .pill-overdue { background: #c2410c; color: #fff; }
            .hero-card, .stat-card, .feed-card, .score-card, .doubt-card, .auth-hero {
                border-radius: 22px;
            }
            .auth-hero {
                background: linear-gradient(135deg, #ff7a45 0%, #ffb347 55%, #7ce7c3 100%);
                color: #1c2430;
                padding: 1.3rem 1.2rem 1.1rem;
                margin-bottom: 1rem;
                box-shadow: 0 12px 30px rgba(255, 90, 54, 0.18);
            }
            .auth-hero h1 { margin: 0; font-size: 2.1rem; }
            .auth-hero p { margin: 0.35rem 0 0; font-weight: 600; }
            .role-badge {
                display: inline-block;
                border-radius: 999px;
                padding: 0.12rem 0.55rem;
                font-size: 0.74rem;
                font-weight: 800;
                margin-left: 0.35rem;
                vertical-align: middle;
            }
            .role-student { background: #d9f5ff; color: #0b5f78; }
            .role-teacher { background: #ffe3c2; color: #9a4100; }
            .subject-wrap { display: inline; }
            .subject-chip {
                display: inline-block;
                margin-left: 0.25rem;
                background: #1a73e8;
                color: #fff;
                border-radius: 999px;
                font-size: 0.68rem;
                font-weight: 800;
                padding: 0.08rem 0.45rem;
            }
            .feed-important { box-shadow: 0 0 0 2px #f4b400, 0 8px 18px rgba(244, 180, 0, 0.25); }
            .pill-private { background: #5b21b6; color: #fff; }
            .pill-star { background: #f4b400; color: #3b2500; }
            .dash-top { display: flex; justify-content: space-between; gap: 0.8rem; align-items: flex-start; flex-wrap: nowrap; }
            .welcome-kicker { font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; color: #ff5a36; font-size: 0.78rem; }
            .welcome-id { font-size: 2.05rem; font-weight: 900; margin: 0.1rem 0 0.35rem; line-height: 1.1; color: #171f2a; }
            .icon-row { display: flex; justify-content: flex-end; gap: 0.35rem; flex: 0 0 auto; }
            div[data-testid="stHorizontalBlock"]:has(.action-slot) {
                flex-wrap: wrap !important;
                align-items: stretch;
                gap: 0.45rem;
            }
            .action-slot { min-width: 140px; flex: 1 1 140px; }
            .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin: 0.8rem 0 1rem; }
            .stat-card {
                background: #fff;
                border: 1px solid #ffd7b0;
                padding: 0.9rem 1rem;
                box-shadow: 0 8px 18px rgba(255, 122, 69, 0.08);
            }
            .stat-label { font-size: 0.78rem; font-weight: 800; text-transform: uppercase; color: #9a4100; }
            .stat-value { font-size: 1.55rem; font-weight: 900; color: #171f2a; }
            .level-bar { height: 10px; background: #ffe1c8; border-radius: 999px; overflow: hidden; margin-top: 0.45rem; }
            .level-fill { height: 100%; background: linear-gradient(90deg, #ff5a36, #ffb347); }
            .feed-card, .doubt-card {
                padding: 0.85rem 0.95rem;
                margin: 0.45rem 0;
            }
            .feed-open {
                background: linear-gradient(180deg, #fff4d6, #ffe7b0);
                border: 2px solid #f4b400;
            }
            .feed-solved {
                background: linear-gradient(180deg, #e7fff4, #c4f5de);
                border: 2px solid #12a06a;
            }
            .feed-mine {
                background: linear-gradient(180deg, #efe7ff, #ddd0ff);
                border: 2px solid #7b5cff;
            }
            .feed-open .feed-meta { color: #9a4100; }
            .feed-solved .feed-meta { color: #0b6b45; }
            .feed-mine .feed-meta { color: #4c2fb3; }
            .feed-pill {
                display: inline-block;
                border-radius: 999px;
                font-size: 0.72rem;
                font-weight: 800;
                padding: 0.08rem 0.5rem;
                margin-bottom: 0.3rem;
            }
            .pill-open { background: #f4b400; color: #3b2500; }
            .pill-solved { background: #12a06a; color: #fff; }
            .pill-mine { background: #7b5cff; color: #fff; }
            .pill-answer { background: #1a73e8; color: #fff; }
            .feed-question {
                background: linear-gradient(180deg, #fff7e4, #ffe4b8);
                border: 2px solid #f0a202;
            }
            .feed-answer {
                background: linear-gradient(180deg, #eef5ff, #d5e8ff);
                border: 2px solid #1a73e8;
                margin-top: 0.35rem;
            }
            .blue-tick {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 1.35rem;
                height: 1.35rem;
                margin-left: 0.35rem;
                border-radius: 999px;
                background: #1a73e8;
                color: #fff;
                font-size: 0.85rem;
                font-weight: 900;
                vertical-align: middle;
            }
            .feed-meta { font-weight: 800; font-size: 0.82rem; }
            .hero-card {
                background: linear-gradient(135deg, #fff4cc 0%, #ffe08a 42%, #bff5e0 100%);
                border: 1px solid #e4c56a;
                padding: 1.05rem 1rem 0.95rem;
                text-align: center;
                margin: 0.2rem 0 1rem;
            }
            .wizard-trophy { font-size: 2.8rem; line-height: 1; }
            .wizard-kicker {
                margin-top: 0.25rem;
                font-size: 0.78rem;
                font-weight: 800;
                letter-spacing: 0.04em;
                text-transform: uppercase;
                color: #8a5a12;
            }
            .wizard-name { margin: 0.15rem 0 0.1rem; font-size: 1.45rem; font-weight: 900; color: #1f3d34; }
            .wizard-points { color: #c2410c; font-weight: 800; }
            .wizard-empty {
                background: #fff8ee;
                border: 1px dashed #e0c48a;
                border-radius: 14px;
                padding: 0.85rem 1rem;
                color: #6b5344;
                margin: 0.2rem 0 1rem;
            }
            .score-card {
                display: flex;
                gap: 0.75rem;
                align-items: center;
                background: #fffdf8;
                border: 1px solid #eadfce;
                padding: 0.75rem 0.85rem;
                margin: 0.45rem 0;
            }
            .score-card.rank-1 { background: linear-gradient(90deg, #fff6d2, #fffdf8); border-color: #e6c35a; }
            .score-card.rank-2 { background: linear-gradient(90deg, #f3f4f6, #fffdf8); border-color: #c5ccd4; }
            .score-card.rank-3 { background: linear-gradient(90deg, #fbe8dc, #fffdf8); border-color: #e0b08a; }
            .score-card.mine { box-shadow: inset 0 0 0 2px #ff5a36; }
            .score-medal { font-size: 1.7rem; width: 2.2rem; text-align: center; flex: 0 0 auto; }
            .score-name { font-weight: 800; font-size: 1.05rem; color: #1c2b26; }
            .score-points { color: #c2410c; font-weight: 800; font-size: 0.92rem; }
            .score-badges { color: #5c5146; font-size: 0.88rem; margin-top: 0.12rem; }
            .you-tag {
                display: inline-block;
                margin-left: 0.35rem;
                background: #ff5a36;
                color: #fff;
                border-radius: 999px;
                font-size: 0.72rem;
                font-weight: 800;
                padding: 0.05rem 0.45rem;
                vertical-align: middle;
            }
            @media (max-width: 768px) {
                .block-container { padding-left: 0.8rem; padding-right: 0.8rem; }
                .welcome-id { font-size: 1.35rem; }
                .stat-grid { grid-template-columns: 1fr; }
                div[data-testid="stHorizontalBlock"]:has(.action-slot) { flex-direction: column !important; }
                .action-slot { min-width: 100%; }
                .action-slot .stButton > button { width: 100%; min-height: 3.1rem; }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


LOGO_PATH = os.path.join(BASE_DIR, "logo.png")
if not os.path.exists(LOGO_PATH):
    LOGO_PATH = os.path.join(BASE_DIR, "logo.svg")


def render_welcome():
    st.markdown('<div class="splash">', unsafe_allow_html=True)
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=148)
    st.markdown(
        "<h1>EduMatch</h1><p>Classmates help classmates. Teachers check the answers.</p></div>",
        unsafe_allow_html=True,
    )
    if st.button("🚀 Let's go!", type="primary", **stretch_kwargs(st.button)):
        st.session_state["auth_screen"] = "signin"
        st.rerun()


def render_auth(data):
    auth_screen = st.session_state.get("auth_screen", "welcome")
    if auth_screen == "welcome":
        render_welcome()
        return
    show_flash()
    if auth_screen == "signin":
        if st.button("⬅️ Back", type="tertiary"):
            st.session_state["auth_screen"] = "welcome"
            st.rerun()
        st.markdown(
            '<div class="auth-hero"><h1>Sign in</h1><p>Enter your User ID and 4-digit passkey.</p></div>',
            unsafe_allow_html=True,
        )
        with st.form("sign_in"):
            username = st.text_input("User ID", placeholder="Enter a registered ID")
            passkey = st.text_input("Passkey", type="password", max_chars=4, placeholder="4 digits")
            submitted = st.form_submit_button("🔑 Sign in", type="primary", **stretch_kwargs(st.form_submit_button))
        if submitted:
            if not valid_username(username):
                st.error(USERNAME_HINT)
            else:
                user = find_user_by_username(data, username)
                if user and passkey_matches(user, passkey):
                    st.session_state["user_id"] = user["id"]
                    st.session_state["screen"] = "home"
                    st.rerun()
                st.error("That User ID and passkey do not match.")
        if st.button("✨ Create a new account"):
            st.session_state["auth_screen"] = "signup"
            st.rerun()
        return

    if st.button("⬅️ Back", type="tertiary"):
        st.session_state["auth_screen"] = "signin"
        st.rerun()
    st.markdown(
        '<div class="auth-hero"><h1>Create account</h1><p>Pick a User ID and a 4-digit passkey.</p></div>',
        unsafe_allow_html=True,
    )
    as_teacher = st.checkbox("Create as teacher")
    teacher_subjects_pick = []
    if as_teacher:
        st.caption("Select the subjects you teach.")
        teacher_subjects_pick = subject_checkboxes("signup")
    with st.form("create_account"):
        username = st.text_input("User ID", placeholder="new.user1")
        passkey = st.text_input("Choose a passkey", type="password", max_chars=4, placeholder="4 digits")
        created = st.form_submit_button("🎉 Create my account", type="primary", **stretch_kwargs(st.form_submit_button))
    if created:
        clean = username.strip()
        role = "teacher" if as_teacher else "student"
        if not valid_username(clean):
            st.warning(USERNAME_HINT)
        elif find_user_by_username(data, clean):
            st.warning("That User ID is already in use. Sign in instead.")
        elif not valid_passkey(passkey):
            st.warning("The passkey must be 4 digits.")
        elif role == "teacher" and not teacher_subjects_pick:
            st.warning("Choose at least one subject you teach.")
        else:
            user = {
                "id": next_id(data["users"], "u"),
                "name": clean,
                "username": clean,
                "role": role,
                "points": 0,
                "badges": [],
                "subjects": list(teacher_subjects_pick) if role == "teacher" else [],
            }
            if role == "teacher":
                if len(verified_teachers(data)) < TEACHER_APPROVALS_NEEDED:
                    user["status"] = "verified"
                    user["approvals"] = []
                    notice = "Account created. You are a verified bootstrap teacher."
                else:
                    user["status"] = "pending"
                    user["approvals"] = []
                    notice = "Teacher account created. Two verified teachers must approve you before you can answer doubts."
            else:
                user["status"] = "active"
                notice = "Account created. You are signed in as a student."
            set_passkey(user, passkey.strip())
            data["users"].append(user)
            save_data(data)
            st.session_state["user_id"] = user["id"]
            st.session_state["screen"] = "home"
            flash(notice)
            st.rerun()


def icon_bar(user):
    is_teacher = user["role"] == "teacher"
    st.markdown(
        (
            '<div class="welcome-kicker">Welcome</div>'
            f'<div class="welcome-id">{html.escape(username_of(user))} {role_badge(user["role"], user=user)}</div>'
        ),
        unsafe_allow_html=True,
    )
    a, b, c = st.columns(3, gap="small")
    with a:
        st.markdown('<div class="action-slot action-ask">', unsafe_allow_html=True)
        if is_teacher:
            if st.button("🛠️ Resolve a doubt", **stretch_kwargs(st.button)):
                go("help")
        else:
            if st.button("❓ Ask a doubt", **stretch_kwargs(st.button)):
                go("ask")
        st.markdown("</div>", unsafe_allow_html=True)
    with b:
        help_class = "action-scores" if is_teacher else "action-help"
        st.markdown(f'<div class="action-slot {help_class}">', unsafe_allow_html=True)
        if is_teacher:
            if st.button("🏆 Scores", **stretch_kwargs(st.button)):
                go("scores")
        else:
            if st.button("✏️ Help a classmate", **stretch_kwargs(st.button)):
                go("help")
        st.markdown("</div>", unsafe_allow_html=True)
    with c:
        st.markdown('<div class="action-slot action-out">', unsafe_allow_html=True)
        if st.button("🚪 Sign out", **stretch_kwargs(st.button)):
            sign_out()
        st.markdown("</div>", unsafe_allow_html=True)


def verified_mark(answer):
    if answer and answer.get("verified"):
        return '<span class="blue-tick" title="Teacher verified">✓</span>'
    return ""


def extra_pills(doubt):
    bits = []
    if doubt.get("private"):
        bits.append('<span class="feed-pill pill-private">Private</span>')
    if doubt.get("important"):
        bits.append('<span class="feed-pill pill-star">★ Important</span>')
    attachments = doubt.get("attachments") or []
    if any((item.get("kind") == "audio") for item in attachments):
        bits.append('<span class="feed-pill pill-mine">🎤 Voice</span>')
    elif attachments:
        bits.append('<span class="feed-pill pill-mine">📷 Photo</span>')
    if is_overdue(doubt):
        bits.append('<span class="feed-pill pill-overdue">Overdue</span>')
    return "".join(bits)


def doubt_preview(doubt, data, key, kind="open"):
    asker = find_user(data, doubt["student_id"])
    label = username_of(asker) if asker else doubt.get("student_username") or doubt.get("student_name", "")
    role = asker["role"] if asker else "student"
    snippet = doubt["question"][:90] + ("…" if len(doubt["question"]) > 90 else "")
    kind_class = {"open": "feed-open", "solved": "feed-solved", "mine": "feed-mine"}[kind]
    if doubt.get("important"):
        kind_class += " feed-important"
    pill = {"open": ("pill-open", "Waiting"), "solved": ("pill-solved", "Solved"), "mine": ("pill-mine", "Yours")}[kind]
    latest = doubt["answers"][-1] if doubt.get("answers") else None
    tick = verified_mark(latest)
    st.markdown(
        (
            f'<div class="feed-card {kind_class}">'
            f'<span class="feed-pill {pill[0]}">{pill[1]}{tick}</span>{extra_pills(doubt)}'
            f'<div class="feed-meta">{html.escape(doubt["subject"])} · '
            f'{html.escape(label)} {role_badge(role, user=asker)}</div>'
            f"<div>{html.escape(snippet)}</div></div>"
        ),
        unsafe_allow_html=True,
    )
    render_attachment_list(doubt.get("attachments", []))
    if st.button("👀 Open", key=key, **stretch_kwargs(st.button)):
        go("doubt", doubt["id"])


def render_home(data, user):
    show_flash()
    icon_bar(user)
    floor, level_name, emoji = level_for(user["points"])
    upcoming = next_level(user["points"])
    if upcoming:
        span = max(upcoming[0] - floor, 1)
        fill = min(100, int(((user["points"] - floor) / span) * 100))
        next_line = f"{upcoming[0] - user['points']} points to {upcoming[1]}"
    else:
        fill = 100
        next_line = "Top level unlocked"
    st.markdown(
        (
            '<div class="stat-grid">'
            f'<div class="stat-card"><div class="stat-label">Points</div>'
            f'<div class="stat-value">{user["points"]}</div></div>'
            f'<div class="stat-card"><div class="stat-label">Level</div>'
            f'<div class="stat-value">{emoji} {level_name}</div>'
            f'<div class="level-bar"><div class="level-fill" style="width:{fill}%"></div></div>'
            f'<div class="feed-meta">{html.escape(next_line)}</div></div></div>'
        ),
        unsafe_allow_html=True,
    )
    extras = st.columns(2 if is_verified_teacher(user) else 1)
    with extras[0]:
        st.markdown('<div class="action-slot action-scores">', unsafe_allow_html=True)
        if st.button("🏆 Scores", key="home-scores", **stretch_kwargs(st.button)):
            go("scores")
        st.markdown("</div>", unsafe_allow_html=True)
    if is_verified_teacher(user):
        with extras[1]:
            st.markdown('<div class="action-slot action-check">', unsafe_allow_html=True)
            if st.button("✅ Check answers", key="home-check", **stretch_kwargs(st.button)):
                go("check")
            st.markdown("</div>", unsafe_allow_html=True)

    subject_choice = st.selectbox("Subject filter", ["All subjects"] + SUBJECTS, key="home_subject_filter")

    if is_pending_teacher(user):
        votes = len(user.get("approvals", []))
        st.markdown(
            (
                '<div class="pending-banner">Your teacher account is pending. '
                f"{votes}/{TEACHER_APPROVALS_NEEDED} teacher approvals so far. "
                "You can browse, but you cannot answer or check doubts yet.</div>"
            ),
            unsafe_allow_html=True,
        )
    if user.get("status") == "rejected":
        st.error("This teacher account was rejected. Ask a verified teacher if you need a new review.")

    if is_verified_teacher(user):
        waiting = pending_teacher_accounts(data)[:3]
        if waiting:
            st.markdown("### Teacher approvals")
            st.info("Confirm whether these new teacher accounts are genuine.")
            for candidate in waiting:
                with st.container(border=True):
                    st.markdown(
                        f"**{html.escape(username_of(candidate))}** {role_badge('teacher', user=candidate)} · "
                        f"{len(candidate.get('approvals', []))}/{TEACHER_APPROVALS_NEEDED} approvals",
                        unsafe_allow_html=True,
                    )
                    if user["id"] in candidate.get("approvals", []):
                        st.caption("You already approved this account.")
                    elif candidate["id"] == user["id"]:
                        st.caption("Waiting for other teachers.")
                    else:
                        yes, no = st.columns(2)
                        with yes:
                            st.markdown('<div class="action-slot action-approve">', unsafe_allow_html=True)
                            if st.button("👍 Approve", key=f"approve-{candidate['id']}", **stretch_kwargs(st.button)):
                                result = approve_teacher(data, candidate, user)
                                save_data(data)
                                flash("This teacher is now verified." if result == "verified" else "Approval saved.")
                                st.rerun()
                            st.markdown("</div>", unsafe_allow_html=True)
                        with no:
                            st.markdown('<div class="action-slot action-reject">', unsafe_allow_html=True)
                            if st.button("👎 Reject", key=f"reject-{candidate['id']}", **stretch_kwargs(st.button)):
                                candidate["status"] = "rejected"
                                save_data(data)
                                flash(f"{username_of(candidate)} was rejected.")
                                st.rerun()
                            st.markdown("</div>", unsafe_allow_html=True)

        overdue = [
            doubt
            for doubt in visible_doubts(data, user)
            if is_overdue(doubt) and (not teacher_subjects(user) or doubt.get("subject") in teacher_subjects(user))
        ]
        notices = unread_teacher_notices(user)
        if notices:
            st.markdown("### 🔔 Teacher alerts")
            for notice in notices[-8:][::-1]:
                st.markdown(
                    f'<div class="overdue-banner">{html.escape(notice.get("message", "New update"))}</div>',
                    unsafe_allow_html=True,
                )
                cols = st.columns([3, 1])
                with cols[0]:
                    if notice.get("doubt_id") and st.button("👀 Open doubt", key=f"notice-{notice['id']}"):
                        notice["read"] = True
                        save_data(data)
                        go("doubt", notice["doubt_id"])
                with cols[1]:
                    if st.button("✔️ Seen", key=f"seen-{notice['id']}"):
                        notice["read"] = True
                        save_data(data)
                        st.rerun()
        if overdue:
            st.markdown(
                (
                    f'<div class="overdue-banner">Overdue (&gt;{OVERDUE_MINUTES} min): '
                    f"{len(overdue)} unanswered doubt{'s' if len(overdue) != 1 else ''} in your subject"
                    f"{'s' if len(overdue) != 1 else ''}.</div>"
                ),
                unsafe_allow_html=True,
            )

    if user["role"] == "teacher" and not teacher_subjects(user):
        st.info("Pick the subjects you teach so private doubts reach the right teachers.")
        picked = subject_checkboxes("home_teacher")
        if st.button("💾 Save subjects", type="primary"):
            if not picked:
                st.warning("Choose at least one subject.")
            else:
                user["subjects"] = list(picked)
                save_data(data)
                flash("Teaching subjects saved.")
                st.rerun()

    only_important = st.checkbox("Show important doubts only", key="home_important")
    feed = visible_doubts(data, user)
    if subject_choice != "All subjects":
        feed = [doubt for doubt in feed if doubt.get("subject") == subject_choice]
    if only_important:
        feed = [doubt for doubt in feed if doubt.get("important")]

    def sorted_feed(items):
        return sorted(
            items,
            key=lambda doubt: (doubt.get("important", False), doubt["created_at"]),
            reverse=True,
        )

    open_doubts = sorted_feed([doubt for doubt in feed if doubt["status"] == "Open"])[:6]
    solved = sorted_feed([doubt for doubt in feed if doubt["status"] == "Resolved"])[:6]
    mine = sorted_feed([doubt for doubt in feed if doubt["student_id"] == user["id"]])

    asked_title = "Recently asked by students" if user["role"] == "teacher" else "Recently asked"
    st.markdown(f"### 🆕 {asked_title}")
    if not open_doubts:
        st.caption("No open doubts right now.")
    for doubt in open_doubts:
        doubt_preview(doubt, data, f"recent-open-{doubt['id']}", "open")

    st.markdown("### ✅ Recently solved")
    if not solved:
        st.caption("No solved doubts yet.")
    for doubt in solved:
        doubt_preview(doubt, data, f"recent-solved-{doubt['id']}", "solved")

    if user["role"] == "student":
        st.markdown("### 📌 My doubts")
        if not mine:
            st.caption("You have not asked a doubt yet.")
        for doubt in mine:
            doubt_preview(doubt, data, f"mine-{doubt['id']}", "mine")


def back_home():
    if st.button("⬅️ Back", type="tertiary"):
        go_back()


def render_question_card(doubt, data):
    asker = find_user(data, doubt["student_id"])
    label = username_of(asker) if asker else doubt.get("student_username") or doubt.get("student_name", "")
    role = asker["role"] if asker else "student"
    extra_class = " feed-important" if doubt.get("important") else ""
    st.markdown(
        (
            f'<div class="feed-card feed-question{extra_class}">'
            f'<span class="feed-pill pill-open">Doubt</span>{extra_pills(doubt)}'
            f'<div class="feed-meta">{html.escape(doubt["subject"])} · {html.escape(label)} {role_badge(role, user=asker)}</div>'
            f"<div>{html.escape(doubt['question'])}</div></div>"
        ),
        unsafe_allow_html=True,
    )
    render_attachment_list(doubt.get("attachments", []))


def render_answer_card(answer, data):
    author = find_user(data, answer["author_id"])
    who = username_of(author) if author else answer.get("author_username") or answer.get("author_name", "")
    author_role = author["role"] if author else "student"
    tick = verified_mark(answer)
    tag = "Teacher verified" if answer.get("verified") else "Needs a teacher check"
    st.markdown(
        (
            '<div class="feed-card feed-answer">'
            f'<span class="feed-pill pill-answer">Answer{tick}</span>'
            f'<div class="feed-meta">{html.escape(who)} {role_badge(author_role, user=author)} · {html.escape(tag)}</div>'
            f"<div>{html.escape(answer['explanation'])}</div></div>"
        ),
        unsafe_allow_html=True,
    )
    render_attachment_list(answer.get("attachments", []))


def render_doubt(data, user):
    show_flash()
    back_home()
    doubt = next((item for item in data["doubts"] if item["id"] == st.session_state.get("open_doubt_id")), None)
    if not doubt:
        st.info("That doubt is gone.")
        return
    if not can_see_doubt(user, doubt):
        st.info("This doubt is private to teachers of that subject.")
        return
    st.subheader(doubt["subject"])
    if is_overdue(doubt):
        st.markdown(
            f'<div class="overdue-banner">Overdue (&gt;{OVERDUE_MINUTES} min) — teachers of this subject have been notified.</div>',
            unsafe_allow_html=True,
        )
    if is_verified_teacher(user):
        star_label = "⭐ Mark as important" if not doubt.get("important") else "🤍 Remove important"
        if st.button(star_label, key=f"star-{doubt['id']}"):
            doubt["important"] = not doubt.get("important", False)
            save_data(data)
            flash("Marked as important." if doubt["important"] else "Removed the important mark.")
            st.rerun()
    render_question_card(doubt, data)
    if not doubt["answers"]:
        st.caption("Waiting for an answer")
        if user["role"] == "student" and doubt["student_id"] != user["id"]:
            render_answer_form(data, user, doubt)
        elif is_verified_teacher(user):
            render_answer_form(data, user, doubt)
        elif is_pending_teacher(user):
            st.warning("Your teacher account is pending approval, so you cannot answer yet.")
        return
    for answer in doubt["answers"]:
        render_answer_card(answer, data)
        author = find_user(data, answer["author_id"])
        author_role = author["role"] if author else "student"
        if is_verified_teacher(user) and author_role == "student":
            teacher_answer_tools(data, user, doubt, answer)


def render_ask(data, user):
    show_flash()
    back_home()
    st.subheader("Ask a doubt")
    if user["role"] != "student":
        st.info("Teachers check and answer doubts. Students ask them.")
        return
    if "subject" not in st.session_state:
        st.session_state["subject"] = SUBJECTS[0]
    st.session_state["subject"] = st.selectbox(
        "Subject",
        SUBJECTS,
        index=SUBJECTS.index(st.session_state["subject"]) if st.session_state["subject"] in SUBJECTS else 0,
    )
    ask_privately = st.toggle("Ask teacher privately", help="Only teachers of this subject will see it.")
    st.caption("📷 Photos, files, and 🎤 voice notes are saved on the doubt so teachers and classmates can see and hear them.")
    files = st.file_uploader(
        "📎 Attach files, photos, or voice",
        accept_multiple_files=True,
        type=["png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "doc", "docx", "mp3", "wav", "m4a", "ogg", "webm"],
        key="ask_files",
    )
    camera = st.camera_input("📷 Take a picture", key="ask_camera")
    voice = maybe_audio_input("ask_voice")
    previews = list(files or [])
    if camera is not None:
        previews.append(camera)
    if voice is not None:
        previews.append(voice)
    if previews:
        st.caption("Attached — everyone who can open this doubt will see these.")
        for index, item in enumerate(previews):
            name = getattr(item, "name", f"file-{index + 1}")
            mime = getattr(item, "type", "") or ""
            kind = attachment_kind(name, mime)
            if kind == "image":
                st.image(item, caption=name, width=180)
            elif kind == "audio":
                st.audio(item)
                st.caption(f"🎤 {name}")
            else:
                st.write(f"📎 {name}")
    with st.form("ask_form", clear_on_submit=True):
        question = st.text_area(
            "Question",
            height=140,
            placeholder="What is confusing, and what have you already tried?",
        )
        posted = st.form_submit_button("🚀 Post my doubt", type="primary", **stretch_kwargs(st.form_submit_button))
    if posted:
        text = question.strip()
        if doubts_posted_today(data, user["id"]) >= DAILY_DOUBT_LIMIT:
            st.error("Daily limit reached: You can only ask 6 doubts per day.")
        elif len(text) < 10:
            st.warning("Write a little more so a classmate knows how to help.")
        else:
            attachments = []
            for upload in files or []:
                attachments.append(save_upload(upload, "file"))
            if camera is not None:
                attachments.append(save_upload(camera, "cam"))
            if voice is not None:
                attachments.append(save_upload(voice, "voice"))
            doubt = {
                "id": next_id(data["doubts"], "d"),
                "student_id": user["id"],
                "student_name": user.get("name", username_of(user)),
                "student_username": username_of(user),
                "subject": st.session_state["subject"],
                "question": text,
                "status": "Open",
                "created_at": now_stamp(),
                "answers": [],
                "private": bool(ask_privately),
                "important": False,
                "attachments": attachments,
            }
            data["doubts"].append(doubt)
            notify_teachers_for_doubt(
                data,
                doubt,
                "new",
                f"New {doubt['subject']} doubt from {username_of(user)}. Photo and voice notes are on the post.",
            )
            save_data(data)
            if ask_privately:
                flash(f"Posted privately. Teachers of {st.session_state['subject']} were notified.")
            else:
                flash("Posted. Teachers of this subject were notified, and classmates can see the photos too.")
            st.rerun()

    st.markdown("#### My doubts")
    mine = [doubt for doubt in data["doubts"] if doubt["student_id"] == user["id"]]
    mine.sort(key=lambda doubt: doubt["created_at"], reverse=True)
    if not mine:
        st.caption("You have not asked a doubt yet.")
    for doubt in mine:
        with st.container(border=True):
            st.markdown(f"**{doubt['subject']}**")
            st.write(doubt["question"])
            render_attachment_list(doubt.get("attachments", []))
            if doubt["answers"]:
                latest = doubt["answers"][-1]
                render_answer_card(latest, data)
            else:
                st.caption("Waiting for an answer")


def render_answer_form(data, user, doubt):
    if user.get("role") == "teacher" and not is_verified_teacher(user):
        st.warning("Pending teachers cannot answer doubts until two teachers approve the account.")
        return
    st.caption("You can attach a photo or a 🎤 voice note with your answer.")
    answer_files = st.file_uploader(
        "📎 Answer files",
        accept_multiple_files=True,
        type=["png", "jpg", "jpeg", "gif", "webp", "mp3", "wav", "m4a", "ogg", "webm"],
        key=f"ans-files-{doubt['id']}",
    )
    answer_voice = maybe_audio_input(f"ans-voice-{doubt['id']}")
    with st.form(f"answer-{doubt['id']}", clear_on_submit=True):
        explanation = st.text_area("Type your answer", height=120, key=f"answer-text-{doubt['id']}")
        sent = st.form_submit_button("💡 Send answer", type="primary", **stretch_kwargs(st.form_submit_button))
    if sent:
        text = explanation.strip()
        if len(text) < 10:
            st.warning("Add a step-by-step answer before sending.")
            return
        points = TEACHER_SOLVE_POINTS if user["role"] == "teacher" else STUDENT_SOLVE_POINTS
        badge = "Master Mentor" if user["role"] == "teacher" else "Contributor"
        attachments = []
        for upload in answer_files or []:
            attachments.append(save_upload(upload, "ans"))
        if answer_voice is not None:
            attachments.append(save_upload(answer_voice, "ansvoice"))
        doubt["answers"].append(
            {
                "id": next_id(
                    [answer for item in data["doubts"] for answer in item["answers"]],
                    "a",
                ),
                "author_id": user["id"],
                "author_name": user.get("name", username_of(user)),
                "author_username": username_of(user),
                "explanation": text,
                "points_awarded": points,
                "verified": user["role"] == "teacher",
                "upvotes": [],
                "attachments": attachments,
            }
        )
        doubt["status"] = "Resolved"
        award_points(data, user, points, "Answered a doubt", badge)
        save_data(data)
        flash(f"Answer sent. You earned {points} points.")
        st.rerun()


def render_help(data, user):
    show_flash()
    back_home()
    heading = "Help a student" if user["role"] == "teacher" else "Help a classmate"
    st.subheader(heading)
    if is_pending_teacher(user):
        st.warning("Your teacher account is pending approval. You can read doubts, but you cannot answer yet.")
    subject_choice = st.selectbox("Subject filter", ["All subjects"] + SUBJECTS, key="help_subject_filter")
    only_important = st.checkbox("Show important doubts only", key="help_important")
    open_doubts = [doubt for doubt in visible_doubts(data, user) if doubt["status"] == "Open"]
    if subject_choice != "All subjects":
        open_doubts = [doubt for doubt in open_doubts if doubt.get("subject") == subject_choice]
    if only_important:
        open_doubts = [doubt for doubt in open_doubts if doubt.get("important")]
    open_doubts.sort(key=lambda doubt: (doubt.get("important", False), doubt["created_at"]), reverse=True)
    if not open_doubts:
        st.caption("No open doubts right now.")
        return
    for doubt in open_doubts:
        render_question_card(doubt, data)
        if doubt["student_id"] == user["id"] and user["role"] == "student":
            st.caption("Waiting for a classmate")
            continue
        render_answer_form(data, user, doubt)


def teacher_answer_tools(data, user, doubt, answer):
    author = find_user(data, answer["author_id"])
    author_label = username_of(author) if author else answer.get("author_username") or answer.get("author_name", "")
    edit_key = f"edit-{answer['id']}"
    cols = st.columns(4)
    with cols[3]:
        star_label = "⭐ Important" if not doubt.get("important") else "🤍 Unstar"
        if st.button(star_label, key=f"star-ans-{answer['id']}"):
            doubt["important"] = not doubt.get("important", False)
            save_data(data)
            flash("Marked as important." if doubt["important"] else "Removed the important mark.")
            st.rerun()
    with cols[0]:
        if st.button("✏️ Edit", key=f"edit-btn-{answer['id']}", **stretch_kwargs(st.button)):
            st.session_state[edit_key] = True
            st.rerun()
    with cols[1]:
        if st.button("🗑️ Delete", key=f"delete-{answer['id']}", **stretch_kwargs(st.button)):
            doubt["answers"] = [item for item in doubt["answers"] if item["id"] != answer["id"]]
            if not doubt["answers"]:
                doubt["status"] = "Open"
            save_data(data)
            flash("Answer deleted.")
            st.rerun()
    with cols[2]:
        if not answer.get("verified") and st.button("✅ Verify", key=f"verify-{answer['id']}", type="primary", **stretch_kwargs(st.button)):
            answer["verified"] = True
            answer["points_awarded"] = answer.get("points_awarded", 0) + TEACHER_VERIFY_POINTS
            if author:
                award_points(data, author, TEACHER_VERIFY_POINTS, "Teacher verified a solution")
            save_data(data)
            flash(f"Verified. {author_label} earned {TEACHER_VERIFY_POINTS} points.")
            st.rerun()
    if st.session_state.get(edit_key):
        with st.form(f"edit-form-{answer['id']}"):
            text = st.text_area("Edit answer", value=answer["explanation"], height=120)
            saved = st.form_submit_button("💾 Save", type="primary", **stretch_kwargs(st.form_submit_button))
        if saved:
            cleaned = text.strip()
            if len(cleaned) < 10:
                st.warning("Write a little more before saving.")
            else:
                answer["explanation"] = cleaned
                st.session_state[edit_key] = False
                save_data(data)
                flash("Answer updated.")
                st.rerun()


def pending_checks(data):
    pending = []
    for doubt in data["doubts"]:
        for answer in doubt["answers"]:
            if not answer.get("verified"):
                pending.append((doubt, answer))
    pending.sort(key=lambda pair: pair[0]["created_at"], reverse=True)
    return pending


def render_check(data, user):
    show_flash()
    back_home()
    st.subheader("Check answers")
    if not is_verified_teacher(user):
        st.info("Only a verified teacher can check answers.")
        return
    waiting = [(doubt, answer) for doubt, answer in pending_checks(data) if can_see_doubt(user, doubt)]
    if not waiting:
        st.caption("Nothing to check right now.")
        return
    for doubt, answer in waiting:
        asker = find_user(data, doubt["student_id"])
        author = find_user(data, answer["author_id"])
        asker_label = username_of(asker) if asker else doubt.get("student_username") or doubt.get("student_name", "")
        author_label = username_of(author) if author else answer.get("author_username") or answer.get("author_name", "")
        with st.container(border=True):
            st.markdown(
                f"**{doubt['subject']}** · {html.escape(asker_label)} {role_badge(asker['role'] if asker else 'student', user=asker)} asked",
                unsafe_allow_html=True,
            )
            st.write(doubt["question"])
            st.markdown(
                f"**{html.escape(author_label)}** {role_badge(author['role'] if author else 'student', user=author)} answered",
                unsafe_allow_html=True,
            )
            st.write(answer["explanation"])
            teacher_answer_tools(data, user, doubt, answer)


def medal_for(rank):
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "⭐")


def badge_line(person):
    parts = []
    for badge in person.get("badges", []):
        emoji = BADGE_EMOJI.get(badge, "✨")
        parts.append(f"{emoji} {html.escape(badge)}")
    return " · ".join(parts)


def render_scores(data, user):
    show_flash()
    back_home()
    st.subheader("Scores")
    wizard = weekly_wizard(data)
    week_points = weekly_points(data, wizard["id"]) if wizard else 0
    if wizard and week_points > 0:
        st.markdown(
            (
                '<div class="hero-card">'
                '<div class="wizard-trophy">🏆</div>'
                '<div class="wizard-kicker">Subject Wizard of the Week</div>'
                f'<div class="wizard-name">{html.escape(username_of(wizard))} {role_badge(wizard["role"], user=wizard)}</div>'
                f'<div class="wizard-points">{week_points} points this week</div>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="wizard-empty">🌱 No points yet this week. Help a classmate to start the race.</div>',
            unsafe_allow_html=True,
        )
    for index, person in enumerate(ranked_users(data), start=1):
        rank_class = f" rank-{index}" if index <= 3 else ""
        mine = " mine" if person["id"] == user["id"] else ""
        you = '<span class="you-tag">You</span>' if person["id"] == user["id"] else ""
        badges = badge_line(person)
        badge_html = f'<div class="score-badges">{badges}</div>' if badges else ""
        _, level_name, emoji = level_for(person["points"])
        st.markdown(
            (
                f'<div class="score-card{rank_class}{mine}">'
                f'<div class="score-medal">{medal_for(index)}</div>'
                "<div>"
                f'<div class="score-name">{index}. {html.escape(username_of(person))} {role_badge(person["role"], user=person)}{you}</div>'
                f'<div class="score-points">{person["points"]} points · {emoji} {level_name}</div>'
                f"{badge_html}"
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )


def main():
    page_icon = LOGO_PATH if os.path.exists(LOGO_PATH) else ":sparkles:"
    st.set_page_config(page_title="EduMatch", page_icon=page_icon, layout="centered", initial_sidebar_state="collapsed")
    inject_css()
    data = load_data()
    user_id = st.session_state.get("user_id")
    user = find_user(data, user_id) if user_id else None
    if not user:
        render_auth(data)
        return
    screen = st.session_state.get("screen", "home")
    if screen == "ask":
        render_ask(data, user)
    elif screen == "help":
        render_help(data, user)
    elif screen == "check":
        render_check(data, user)
    elif screen == "scores":
        render_scores(data, user)
    elif screen == "doubt":
        render_doubt(data, user)
    else:
        render_home(data, user)


if __name__ == "__main__":
    main()
