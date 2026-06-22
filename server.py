import os
import re
import json
import sqlite3
from datetime import datetime

import httpx
from google import genai
from google.genai import types
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

load_dotenv()

port = int(os.getenv("PORT", "8000"))
mcp = FastMCP("godsaeng-salon", host="0.0.0.0", port=port)
MODEL = "gemini-2.5-flash"

CATEGORY_EMOJI = {"운동": "💪", "다이어트": "🥗", "공부": "📚", "취미": "🎯", "미라클모닝": "🌅"}
VALID_CATEGORIES = list(CATEGORY_EMOJI.keys())


def get_client():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is not configured.")
    return genai.Client(api_key=api_key)


def extract_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)


DB_PATH = "godsaeng.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                category    TEXT    NOT NULL,
                goal        TEXT    NOT NULL,
                target_days INTEGER DEFAULT 30,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkins (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                goal_id     INTEGER NOT NULL,
                date        TEXT    NOT NULL,
                activity    TEXT,
                score       INTEGER DEFAULT 0,
                comment     TEXT,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                message     TEXT    NOT NULL,
                remind_date TEXT    NOT NULL,
                done        INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


init_db()


def _get_user_goals(user_id: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM goals WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_goal_by_category(user_id: str, category: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM goals WHERE user_id = ? AND category = ? ORDER BY created_at DESC LIMIT 1",
            (user_id, category),
        ).fetchone()
    return dict(row) if row else None


def _count_checkins(goal_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT date) AS cnt FROM checkins WHERE goal_id = ?",
            (goal_id,),
        ).fetchone()
    return row["cnt"] if row else 0


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Productivity Goal",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def set_goal(user_id: str, category: str, goal: str, target_days: int = 30) -> str:
    """Sets a new productivity goal for a user in GodSaeng Salon(갓생살롱). Supported categories: 운동, 다이어트, 공부, 취미, 미라클모닝.

    Args:
        user_id:     User ID (e.g., minjun)
        category:    Goal category — 운동/다이어트/공부/취미/미라클모닝
        goal:        Specific goal description (e.g., 헬스 주 3회, 매일 독서 30분, 매일 6시 기상)
        target_days: Number of days to maintain the goal (default 30)
    """
    if category not in VALID_CATEGORIES:
        return f"카테고리는 {', '.join(VALID_CATEGORIES)} 중 하나여야 해요."

    with get_db() as conn:
        conn.execute(
            "INSERT INTO goals (user_id, category, goal, target_days) VALUES (?, ?, ?, ?)",
            (user_id, category, goal, target_days),
        )
        conn.commit()

    emoji = CATEGORY_EMOJI[category]

    # 자동 마일스톤 리마인더 생성
    from datetime import timedelta
    today = datetime.now().date()
    auto_reminders = []

    milestone_messages = {
        7:  f"{emoji} {category} 7일 달성! 중간 점검할 때야 🔥",
        14: f"{emoji} {category} 2주 완주! 이제 습관이 되고 있어 💪",
        target_days: f"{emoji} {category} {target_days}일 도전 완료! 오늘이 마지막 날이야 🏆",
    }
    for day_offset, msg in milestone_messages.items():
        if day_offset <= target_days:
            remind_date = (today + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            auto_reminders.append((user_id, msg, remind_date))

    if auto_reminders:
        with get_db() as conn:
            conn.executemany(
                "INSERT INTO reminders (user_id, message, remind_date) VALUES (?, ?, ?)",
                auto_reminders,
            )
            conn.commit()

    daily_hint = ""
    if category == "미라클모닝":
        daily_hint = "\n\n매일 아침 몇 시에 알림 드릴까요? `set_daily_reminder`로 설정 가능해요 ⏰"
    elif category == "운동":
        daily_hint = "\n\n매일 운동 시간에 알림 받을래요? `set_daily_reminder`로 설정 가능해요 💪"
    else:
        daily_hint = "\n\n매일 알림 받고 싶으면 `set_daily_reminder`로 설정할 수 있어요!"

    return (
        f"## {emoji} 목표 설정 완료!\n\n"
        f"- **카테고리**: {category}\n"
        f"- **목표**: {goal}\n"
        f"- **기간**: {target_days}일\n\n"
        f"7일, 14일, {target_days}일 마일스톤 알림도 자동 등록했어요 🎯"
        f"{daily_hint}"
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Check In with Photo",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def check_in(image_url: str, user_id: str, category: str) -> str:
    """Verifies goal achievement with a photo for GodSaeng Salon(갓생살롱). Analyzes the photo against the user's active goal and returns an encouraging score and feedback.

    Args:
        image_url: URL of the verification photo
        user_id:   User ID
        category:  Goal category to check in for (운동/다이어트/공부/취미/미라클모닝)
    """
    try:
        goal = _get_goal_by_category(user_id, category)
        if not goal:
            return f"**{category}** 카테고리에 설정된 목표가 없어요. 먼저 목표를 설정해주세요!"

        resp = httpx.get(image_url, follow_redirects=True, timeout=30)
        resp.raise_for_status()

        media_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        today = datetime.now().strftime("%Y-%m-%d")

        client = get_client()
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part(inline_data=types.Blob(mime_type=media_type, data=resp.content)),
                types.Part(text=(
                    f"이 사진을 분석해주세요. 유저의 목표는 [{category}: {goal['goal']}]입니다.\n"
                    "아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.\n\n"
                    "{\n"
                    '  "activity": "사진에서 보이는 활동 (10자 이내)",\n'
                    '  "goal_matched": true 또는 false,\n'
                    '  "score": 0~100 사이 정수,\n'
                    '  "comment": "20자 이내 찰진 응원 한 줄"\n'
                    "}"
                )),
            ],
        )

        result = extract_json(response.text)

        with get_db() as conn:
            conn.execute(
                "INSERT INTO checkins (user_id, goal_id, date, activity, score, comment) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, goal["id"], today, result["activity"], result["score"], result["comment"]),
            )
            conn.commit()

        emoji = CATEGORY_EMOJI.get(category, "✅")
        matched_msg = f"{emoji} 목표 달성 인증 완료!" if result.get("goal_matched") else "오늘도 수고했어! 내일은 더 잘 할 수 있어 💪"

        return (
            f"## 인증 완료 — {user_id}\n\n"
            f"- **활동**: {result['activity']}\n"
            f"- **목표**: {goal['goal']}\n"
            f"- **달성도**: {result['score']}점\n"
            f"- **한 줄 평**: {result['comment']}\n\n"
            f"{matched_msg}"
        )
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Goal Progress",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def get_progress(user_id: str) -> str:
    """Retrieves goal achievement progress and check-in statistics for a user from GodSaeng Salon(갓생살롱). Shows all active goals with completion rates and progress bars.

    Args:
        user_id: User ID
    """
    goals = _get_user_goals(user_id)

    if not goals:
        return f"**{user_id}**님의 목표가 없어요. 먼저 목표를 설정해보세요!"

    lines = [f"## {user_id}님의 갓생 진행 상황\n"]
    for g in goals:
        cnt = _count_checkins(g["id"])
        rate = round(cnt / g["target_days"] * 100)
        emoji = CATEGORY_EMOJI.get(g["category"], "✅")
        filled = min(cnt, 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(
            f"{emoji} **{g['category']}** — {g['goal']}\n"
            f"   {bar} {cnt}/{g['target_days']}일 ({rate}%)\n"
        )

    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Generate Daily Plan",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def generate_daily_plan(user_id: str) -> str:
    """Generates a personalized daily productivity plan using GodSaeng Salon(갓생살롱) based on the user's active goals. Includes time blocks and actionable tasks for today.

    Args:
        user_id: User ID
    """
    goals = _get_user_goals(user_id)

    if not goals:
        return "설정된 목표가 없어요. 먼저 목표를 설정해주세요!"

    goal_summary = "\n".join([f"- {g['category']}: {g['goal']}" for g in goals])
    today = datetime.now().strftime("%Y-%m-%d")

    client = get_client()
    response = client.models.generate_content(
        model=MODEL,
        contents=types.Part(text=(
            f"다음 목표를 가진 사람의 오늘 갓생 플랜을 짜주세요.\n\n"
            f"날짜: {today}\n"
            f"목표:\n{goal_summary}\n\n"
            "조건:\n"
            "- 시간대별 구체적인 행동 계획\n"
            "- 각 목표를 반영한 현실적인 일정\n"
            "- 동기부여 멘트 한 줄\n"
            "- 마크다운 형식, 300자 이내"
        )),
    )

    return response.text


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Daily Reminder",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def set_daily_reminder(user_id: str, message: str, start_date: str, days: int = 30) -> str:
    """Sets a recurring daily reminder for a user in GodSaeng Salon(갓생살롱). Creates individual reminders for each day from start_date over the given number of days. Ideal for daily habits like 미라클모닝(early rising) or workout routines.

    Args:
        user_id:    User ID
        message:    Reminder message (e.g., 미라클모닝! 일어날 시간이야 🌅, 오늘 운동 했어? 💪)
        start_date: First reminder date in YYYY-MM-DD format
        days:       Number of days to repeat (default 30)
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return "날짜 형식이 잘못됐어요. YYYY-MM-DD 형식으로 입력해주세요. (예: 2026-06-28)"

    from datetime import timedelta
    reminders = [
        (user_id, message, (start + timedelta(days=i)).strftime("%Y-%m-%d"))
        for i in range(days)
    ]

    with get_db() as conn:
        conn.executemany(
            "INSERT INTO reminders (user_id, message, remind_date) VALUES (?, ?, ?)",
            reminders,
        )
        conn.commit()

    end_date = (start + timedelta(days=days - 1)).strftime("%Y-%m-%d")
    return (
        f"## ⏰ 매일 알림 설정 완료!\n\n"
        f"- **내용**: {message}\n"
        f"- **기간**: {start_date} ~ {end_date} ({days}일)\n\n"
        f"매일 대화 시작할 때 알림이 뜰 거야. 꾸준히 해보자! 🔥"
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Reminder",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def set_reminder(user_id: str, message: str, remind_date: str) -> str:
    """Saves a reminder for a user in GodSaeng Salon(갓생살롱) to be surfaced on the specified date.

    Args:
        user_id:     User ID
        message:     Reminder content (e.g., 결혼식, 병원 예약, 헬스 등록)
        remind_date: Date to remind in YYYY-MM-DD format (e.g., 2026-06-28)
    """
    try:
        datetime.strptime(remind_date, "%Y-%m-%d")
    except ValueError:
        return "날짜 형식이 잘못됐어요. YYYY-MM-DD 형식으로 입력해주세요. (예: 2026-06-28)"

    with get_db() as conn:
        conn.execute(
            "INSERT INTO reminders (user_id, message, remind_date) VALUES (?, ?, ?)",
            (user_id, message, remind_date),
        )
        conn.commit()

    return (
        f"## ⏰ 리마인더 설정 완료!\n\n"
        f"- **날짜**: {remind_date}\n"
        f"- **내용**: {message}\n\n"
        f"해당 날짜에 대화 시작하면 알려드릴게요!"
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Check Reminders",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def check_reminders(user_id: str) -> str:
    """Checks today's reminders, upcoming schedule, and goal check-in status for a user in GodSaeng Salon(갓생살롱). Always call this at the start of each conversation to proactively surface alerts and streak info.

    Args:
        user_id: User ID
    """
    today = datetime.now().strftime("%Y-%m-%d")

    with get_db() as conn:
        reminder_rows = conn.execute(
            """SELECT * FROM reminders
               WHERE user_id = ? AND done = 0 AND remind_date >= ?
               ORDER BY remind_date ASC LIMIT 10""",
            (user_id, today),
        ).fetchall()

    today_items = []
    upcoming_items = []
    for r in reminder_rows:
        r = dict(r)
        if r["remind_date"] == today:
            today_items.append(r)
        else:
            upcoming_items.append(r)

    # 오늘 체크인 안 한 목표 찾기
    goals = _get_user_goals(user_id)
    unchecked = []
    streaks = []
    for g in goals:
        with get_db() as conn:
            done_today = conn.execute(
                "SELECT 1 FROM checkins WHERE goal_id = ? AND date = ?",
                (g["id"], today),
            ).fetchone()

        if not done_today:
            unchecked.append(g)

        # 연속 달성 스트릭 계산
        with get_db() as conn:
            dates = conn.execute(
                "SELECT DISTINCT date FROM checkins WHERE goal_id = ? ORDER BY date DESC LIMIT 30",
                (g["id"],),
            ).fetchall()
        dates = [r[0] for r in dates]

        streak = 0
        from datetime import timedelta, date as date_type
        check_date = datetime.strptime(today, "%Y-%m-%d").date()
        for d in dates:
            if d == check_date.strftime("%Y-%m-%d"):
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break
        if streak > 1:
            emoji = CATEGORY_EMOJI.get(g["category"], "✅")
            streaks.append(f"{emoji} **{g['category']}** {streak}일 연속 달성 중!")

    # 오늘 것 완료 처리
    if today_items:
        ids = [r["id"] for r in today_items]
        with get_db() as conn:
            conn.execute(
                f"UPDATE reminders SET done = 1 WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()

    lines = []

    if today_items:
        lines.append("## ⏰ 오늘 알림\n")
        for r in today_items:
            lines.append(f"- {r['message']}")
        lines.append("")

    if streaks:
        lines.append("## 🔥 연속 달성\n")
        lines.extend(streaks)
        lines.append("")

    if unchecked:
        lines.append("## 📸 오늘 체크인 대기 중\n")
        for g in unchecked:
            emoji = CATEGORY_EMOJI.get(g["category"], "✅")
            lines.append(f"- {emoji} **{g['category']}** — {g['goal']} (아직 인증 안 했어요!)")
        lines.append("")

    if upcoming_items:
        lines.append("## 📅 다가오는 일정\n")
        for r in upcoming_items[:3]:
            lines.append(f"- **{r['remind_date']}** {r['message']}")

    if not lines:
        return "오늘 알림도 없고 모든 목표 체크인 완료! 갓생 살고 있네 🏆"

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
