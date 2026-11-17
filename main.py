from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime

app = FastAPI(title="Arabic ESPN-Compatible Sports API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ربط رموز ESPN في قالبك بأرقام الدوريات في 365Scores
SLUG_TO_365 = {
    "eng.1": 7,            # الدوري الإنجليزي
    "esp.1": 11,           # الدوري الإسباني
    "ita.1": 17,           # الدوري الإيطالي
    "ger.1": 25,           # الدوري الألماني
    "fra.1": 35,           # الدوري الفرنسي
    "uefa.champions": 572, # دوري أبطال أوروبا
    "ksa.1": 649,          # الدوري السعودي
    "egy.1": 552           # الدوري المصري
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://365scores.com",
    "Origin": "https://365scores.com",
    "Accept": "application/json"
}

def team_logo(comp_id):
    return f"https://365scores.com{comp_id}"

def map_365_status(status_group: int, status_text: str):
    """تحويل حالة 365Scores إلى صيغة ESPN التي يفهمها قالبك"""
    # 2 = لم تبدأ، 3 = جارية الآن، 4 = انتهت
    if status_group == 2:
        return {"state": "pre", "completed": False, "description": status_text or "لم تبدأ"}
    elif status_group == 3:
        state_name = "STATUS_HALFTIME" if "استراحة" in status_text else "STATUS_IN_PROGRESS"
        return {"state": "in", "name": state_name, "completed": False, "description": status_text}
    else:
        return {"state": "post", "name": "STATUS_FINAL", "completed": True, "description": status_text or "انتهت"}

# 1. نقطة نهاية المباريات (مطابقة لـ ESPN Scoreboard)
@app.get("/apis/site/v2/sports/soccer/{league_slug}/scoreboard")
async def get_scoreboard(league_slug: str, dates: str = Query(None)):
    comp_id = SLUG_TO_365.get(league_slug)
    if not comp_id:
        return {"events": []}

    # تحويل تاريخ ESPN (YYYYMMDD) إلى صيغة 365Scores (DD/MM/YYYY)
    if dates and len(dates) == 8:
        dt = datetime.strptime(dates, "%Y%m%d")
    else:
        dt = datetime.now()
    date_str = dt.strftime("%d/%m/%Y")

    url = (
        f"https://365scores.com"
        f"?appTypeId=5&langId=27&timezoneName=Asia/Riyadh"
        f"&sports=1&competitions={comp_id}&startDate={date_str}&endDate={date_str}"
    )

    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=HEADERS, timeout=15.0)
        if res.status_code != 200:
            return {"events": []}
        data = res.json()

    events = []
    for g in data.get("games", []):
        home = g.get("homeCompetitor", {})
        away = g.get("awayCompetitor", {})
        st_info = map_365_status(g.get("statusGroup", 2), g.get("statusText", ""))

        # استخراج الأهداف والبطاقات
        details = []
        for ev in g.get("events", []):
            ev_name = ev.get("eventType", {}).get("name", "")
            is_goal = "هدف" in ev_name
            is_card = "بطاقة" in ev_name
            if is_goal or is_card:
                details.append({
                    "clock": {"displayValue": ev.get("gameTimeDisplay", "")},
                    "type": {"text": ev_name},
                    "scoringPlay": is_goal,
                    "yellowCard": "صفراء" in ev_name,
                    "redCard": "حمراء" in ev_name,
                    "team": {"id": str(ev.get("competitorId"))},
                    "athletesInvolved": [{"displayName": ev.get("playerName", "")}]
                })

        events.append({
            "id": str(g.get("id")),
            "date": g.get("startTime"),
            "status": {
                "displayClock": g.get("gameTimeDisplay", ""),
                "type": st_info
            },
            "competitions": [{
                "id": str(g.get("id")),
                "details": details,
                "competitors": [
                    {
                        "id": str(home.get("id")),
                        "homeAway": "home",
                        "score": str(home.get("score", 0)) if home.get("score", -1) != -1 else "0",
                        "team": {
                            "id": str(home.get("id")),
                            "displayName": home.get("name"),
                            "shortDisplayName": home.get("name"),
                            "logo": team_logo(home.get("id"))
                        }
                    },
                    {
                        "id": str(away.get("id")),
                        "homeAway": "away",
                        "score": str(away.get("score", 0)) if away.get("score", -1) != -1 else "0",
                        "team": {
                            "id": str(away.get("id")),
                            "displayName": away.get("name"),
                            "shortDisplayName": away.get("name"),
                            "logo": team_logo(away.get("id"))
                        }
                    }
                ]
            }]
        })

    return {"events": events}

# 2. نقطة نهاية تفاصيل وإحصائيات المباراة (مطابقة لـ ESPN Summary)
@app.get("/apis/site/v2/sports/soccer/{league_slug}/summary")
async def get_summary(league_slug: str, event: str = Query(...)):
    url = f"https://365scores.com{event}"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=HEADERS, timeout=15.0)
        if res.status_code != 200:
            return {}
        g = res.json().get("game", {})

    home = g.get("homeCompetitor", {})
    away = g.get("awayCompetitor", {})

    # تحويل إحصائيات المباراة (الاستحواذ، التسديدات، الركنيات...)
    home_stats = []
    away_stats = []
    for stat in g.get("statistics", []):
        s_name = stat.get("name", "")
        vals = stat.get("values", ["0", "0"])
        if len(vals) >= 2:
            home_stats.append({"name": s_name, "label": s_name, "displayValue": str(vals)})
            away_stats.append({"name": s_name, "label": s_name, "displayValue": str(vals)})

    return {
        "boxscore": {
            "teams": [
                {"team": {"id": str(home.get("id")), "displayName": home.get("name")}, "statistics": home_stats},
                {"team": {"id": str(away.get("id")), "displayName": away.get("name")}, "statistics": away_stats}
            ]
        }
    }

# 3. نقطة نهاية جدول الترتيب (مطابقة لـ ESPN Standings)
@app.get("/apis/v2/sports/soccer/{league_slug}/standings")
async def get_standings(league_slug: str):
    comp_id = SLUG_TO_365.get(league_slug)
    if not comp_id:
        return {"children": []}

    url = f"https://365scores.com{comp_id}"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=HEADERS, timeout=15.0)
        if res.status_code != 200:
            return {"children": []}
        data = res.json()

    entries = []
    standings_list = data.get("standings", [])
    if standings_list:
        for row in standings_list.get("rows", []):
            comp = row.get("competitor", {})
            entries.append({
                "team": {
                    "id": str(comp.get("id")),
                    "displayName": comp.get("name"),
                    "logos": [{"href": team_logo(comp.get("id"))}]
                },
                "stats": [
                    {"name": "rank", "value": row.get("position"), "displayValue": str(row.get("position"))},
                    {"name": "gamesPlayed", "value": row.get("gamePlayed"), "displayValue": str(row.get("gamePlayed"))},
                    {"name": "wins", "value": row.get("gamesWon"), "displayValue": str(row.get("gamesWon"))},
                    {"name": "ties", "value": row.get("gamesEven"), "displayValue": str(row.get("gamesEven"))},
                    {"name": "losses", "value": row.get("gamesLost"), "displayValue": str(row.get("gamesLost"))},
                    {"name": "pointsFor", "value": row.get("for"), "displayValue": str(row.get("for"))},
                    {"name": "pointsAgainst", "value": row.get("against"), "displayValue": str(row.get("against"))},
                    {"name": "pointDifferential", "value": row.get("for", 0) - row.get("against", 0), "displayValue": str(row.get("for", 0) - row.get("against", 0))},
                    {"name": "points", "value": row.get("points"), "displayValue": str(row.get("points"))}
                ]
            })

    return {"children": [{"standings": {"entries": entries}}]}
