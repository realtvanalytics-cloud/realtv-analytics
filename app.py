"""
Real TV — Telugu News Competitor Tracker
-----------------------------------------
Tracks videos posted TODAY (IST, midnight-to-midnight) by the top Telugu
news channels, so the team can see at a glance what competitors posted and
which videos are gaining traction — without checking each channel by hand.

Two views:
  1. Today's feed      — every video posted since 00:00 IST today
  2. Performance board  — the same videos ranked by a velocity ratio
                          (attention earned per hour since it was posted)

Also: a breaking-news filter and a live-videos-per-channel count.

Deploy on Streamlit Community Cloud. The API key lives in Streamlit Secrets,
never in this file.
"""

from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# India Standard Time = UTC+5:30. The "day" is defined in IST so a mid-day
# refresh only ADDS new videos; nothing shifts out until IST midnight.
IST = timezone(timedelta(hours=5, minutes=30))

# Your father's channel + the top 10 Telugu news competitors.
# Using channel IDs (the "UC..." strings) because they NEVER change —
# handles can be renamed and break. The label is just for display.
# To find any channel's ID: open the channel on YouTube → ... → Share
# channel → Copy channel ID.  Or leave "id" blank and fill "handle".
CHANNELS = [
    {"label": "Real TV (you)",  "handle": "@realtvtelugunews", "id": None},
    {"label": "TV9 Telugu",     "handle": "@tv9telugu",        "id": "UCPXTXMecYqnRKNdqdVOGSFg"},
    {"label": "NTV Telugu",     "handle": "@ntvteluguofficial","id": "UCumtYpCY26F6Jr3satUgMvA"},
    {"label": "TV5 News",       "handle": "@tv5news",           "id": None},
    {"label": "V6 News",        "handle": "@V6News",           "id": None},
    {"label": "Sakshi TV",      "handle": "@SakshiTV",         "id": None},
    {"label": "ABN Telugu",     "handle": "@abntelugutv",      "id": None},
    {"label": "10TV",           "handle": "@10TVNewsTelugu",   "id": None},
    {"label": "Mahaa News",     "handle": "@mahaanews",        "id": "UCDKjhgRoPF1CQk7HluMz23A"},
    {"label": "ETV Andhra",     "handle": "@etvandhrapradesh", "id": None},
    {"label": "HMTV",           "handle": "@hmtvlive",         "id": "UCNZOrs1QBt8cJnv9ud96qRA"},
    {"label": "RTV",            "handle": "@RTVNewsNetwork",     "id: None},
    {"label": "BIGTV",          "handle": "@BIGTVTeluguLive",   "id": None},
]

# Breaking-news keywords (Telugu + English). A title hit flags the video;
# it does NOT hide anything — all videos still show.
BREAKING_KEYWORDS = [
    "బ్రేకింగ్", "బ్రేకింగ", "ప్రత్యేకం", "సంచలనం", "హైలైట్",
    "breaking", "live", "exclusive", "alert", "big news", "urgent",
]

API_BASE = "https://www.googleapis.com/youtube/v3"


# ─────────────────────────────────────────────────────────────────────────────
# API KEY
# ─────────────────────────────────────────────────────────────────────────────
def get_api_key() -> str:
    """Read the key from Streamlit Secrets. Set it in the app's Settings →
    Secrets as:  YT_API_KEY = "your-key-here"  """
    try:
        return st.secrets["YT_API_KEY"]
    except Exception:
        st.error(
            "No API key found. In your Streamlit app go to "
            "**Settings → Secrets** and add a line:  "
            '`YT_API_KEY = "your-key-here"`'
        )
        st.stop()


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING  (cached 10 min so re-runs during the day are fast & cheap)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def resolve_channel(channel: dict, api_key: str) -> dict | None:
    """Resolve a channel by ID (preferred) or @handle into its uploads
    playlist and stats. IDs never change, so they're tried first."""
    label = channel["label"]
    params = {"part": "snippet,contentDetails,statistics", "key": api_key}
    if channel.get("id"):
        params["id"] = channel["id"]
    else:
        params["forHandle"] = channel["handle"].lstrip("@")

    r = requests.get(f"{API_BASE}/channels", params=params, timeout=20)
    if r.status_code != 200:
        return {"label": label, "error": r.json().get("error", {}).get("message", r.text)}
    items = r.json().get("items", [])
    if not items:
        hint = channel.get("id") or channel.get("handle")
        return {"label": label, "error": f"not found ({hint}) — fix id/handle in CHANNELS"}
    c = items[0]
    return {
        "label": label,
        "channel_id": c["id"],
        "title": c["snippet"]["title"],
        "uploads_playlist": c["contentDetails"]["relatedPlaylists"]["uploads"],
        "subs": int(c["statistics"].get("subscriberCount", 0)),
        "error": None,
    }


@st.cache_data(ttl=600, show_spinner=False)
def fetch_recent_uploads(uploads_playlist: str, api_key: str, max_items: int = 15) -> list[str]:
    """Get the most recent video IDs from a channel's uploads playlist."""
    r = requests.get(
        f"{API_BASE}/playlistItems",
        params={
            "part": "contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": max_items,
            "key": api_key,
        },
        timeout=20,
    )
    if r.status_code != 200:
        return []
    return [it["contentDetails"]["videoId"] for it in r.json().get("items", [])]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_video_details(video_ids: list[str], api_key: str) -> list[dict]:
    """Batch-fetch stats + snippet + live status for up to 50 videos at once."""
    if not video_ids:
        return []
    out = []
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        r = requests.get(
            f"{API_BASE}/videos",
            params={
                "part": "snippet,statistics,liveStreamingDetails",
                "id": ",".join(chunk),
                "key": api_key,
            },
            timeout=20,
        )
        if r.status_code == 200:
            out.extend(r.json().get("items", []))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# TRANSFORM
# ─────────────────────────────────────────────────────────────────────────────
def is_breaking(title: str) -> bool:
    t = title.lower()
    return any(k.lower() in t for k in BREAKING_KEYWORDS)


def build_dataframe(api_key: str, like_weight: float):
    """Assemble today's videos across all channels into a tidy DataFrame."""
    now_ist = datetime.now(IST)
    start_of_day_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

    rows = []
    live_counts = {}
    channel_meta = []
    errors = []

    for channel in CHANNELS:
        ch = resolve_channel(channel, api_key)
        if ch is None or ch.get("error"):
            errors.append(f"{channel['label']}: {ch.get('error') if ch else 'unknown error'}")
            continue
        channel_meta.append(ch)
        live_counts[ch["title"]] = 0

        vids = fetch_video_details(
            fetch_recent_uploads(ch["uploads_playlist"], api_key), api_key
        )
        for v in vids:
            published = datetime.fromisoformat(
                v["snippet"]["publishedAt"].replace("Z", "+00:00")
            ).astimezone(IST)

            live = v.get("liveStreamingDetails", {})
            broadcast = v["snippet"].get("liveBroadcastContent", "none")
            # Count anything live now, or that had a live session starting today
            if broadcast == "live":
                live_counts[ch["title"]] += 1
            elif live.get("actualStartTime"):
                started = datetime.fromisoformat(
                    live["actualStartTime"].replace("Z", "+00:00")
                ).astimezone(IST)
                if started >= start_of_day_ist:
                    live_counts[ch["title"]] += 1

            # Keep only videos published since midnight IST today
            if published < start_of_day_ist:
                continue

            stats = v.get("statistics", {})
            views = int(stats.get("viewCount", 0))
            likes = int(stats.get("likeCount", 0))
            hours = max((now_ist - published).total_seconds() / 3600, 0.1)
            ratio = (views + like_weight * likes) / hours

            rows.append(
                {
                    "Channel": ch["title"],
                    "Title": v["snippet"]["title"],
                    "Posted (IST)": published.strftime("%I:%M %p"),
                    "Hrs ago": round(hours, 1),
                    "Views": views,
                    "Likes": likes,
                    "Velocity": round(ratio),
                    "Breaking": "🔴" if is_breaking(v["snippet"]["title"]) else "",
                    "Link": f"https://youtu.be/{v['id']}",
                    "_published": published,
                }
            )

    df = pd.DataFrame(rows)
    return df, live_counts, channel_meta, errors, now_ist


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Real TV — Telugu News Tracker", layout="wide")

st.title("Real TV — Telugu News Competitor Tracker")
st.caption(
    "Videos posted today by the top Telugu news channels. "
    "The day runs midnight-to-midnight IST — refresh anytime and new videos "
    "just add on; nothing drops out until tomorrow."
)

with st.sidebar:
    st.header("Settings")
    like_weight = st.slider(
        "How much a like is worth vs a view",
        min_value=1, max_value=50, value=10,
        help="Velocity = (views + weight × likes) ÷ hours since posted. "
             "Higher weight rewards videos people actively liked.",
    )
    show_breaking_only = st.checkbox("Breaking news only", value=False)
    if st.button("Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data auto-caches for 10 min to stay within the free API quota.")

api_key = get_api_key()

with st.spinner("Fetching today's videos…"):
    df, live_counts, channel_meta, errors, now_ist = build_dataframe(api_key, like_weight)

st.caption(f"Last updated: {now_ist.strftime('%d %b %Y, %I:%M %p IST')}")

if errors:
    with st.expander(f"⚠️ {len(errors)} channel(s) couldn't be read — click to see"):
        for e in errors:
            st.write("•", e)

if df.empty:
    st.info(
        "No videos posted yet today by the tracked channels. "
        "This is normal early in the morning IST — check back later, "
        "or hit Refresh."
    )
    st.stop()

view_df = df[df["Breaking"] == "🔴"] if show_breaking_only else df

# ── Top-line numbers ─────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Videos today", len(view_df))
c2.metric("Breaking-flagged", int((df["Breaking"] == "🔴").sum()))
c3.metric("Total live now / today", sum(live_counts.values()))
c4.metric("Channels active today", df["Channel"].nunique())

tab1, tab2, tab3 = st.tabs(["📋 Today's feed", "🚀 Performance board", "🔴 Live per channel"])

display_cols = ["Breaking", "Channel", "Title", "Posted (IST)", "Views", "Likes", "Velocity", "Link"]

with tab1:
    st.subheader("Everything posted today")
    st.caption("Newest first — this is the 'what did competitors post today' glance.")
    feed = view_df.sort_values("_published", ascending=False)
    st.dataframe(
        feed[display_cols],
        use_container_width=True, hide_index=True,
        column_config={"Link": st.column_config.LinkColumn("Watch", display_text="▶ open")},
    )

with tab2:
    st.subheader("Ranked by velocity — what's catching fire")
    st.caption(
        "Velocity = (views + weight × likes) ÷ hours since posted. "
        "A 2-hour-old video with strong numbers beats an all-day video with weak ones."
    )
    perf = view_df.sort_values("Velocity", ascending=False)
    st.dataframe(
        perf[display_cols],
        use_container_width=True, hide_index=True,
        column_config={"Link": st.column_config.LinkColumn("Watch", display_text="▶ open")},
    )

with tab3:
    st.subheader("Live videos per channel (now or started today)")
    live_df = (
        pd.DataFrame(
            [{"Channel": k, "Live today": v} for k, v in live_counts.items()]
        )
        .sort_values("Live today", ascending=False)
        .reset_index(drop=True)
    )
    st.dataframe(live_df, use_container_width=True, hide_index=True)

st.divider()
st.caption(
    "Note: view/like counts are current totals. For videos posted today that "
    "equals today's performance. Tracking how yesterday's videos grow overnight "
    "would need stored history — a clean future upgrade."
)
