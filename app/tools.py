from __future__ import annotations

"""External tools that provide real-time context to the model.

The routes call run_tools() before invoking the LLM. Tool outputs are converted
to a temporary system message, so this works even with providers that do not
support native function calling.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

import httpx
from langchain_core.tools import tool

from app.config import Settings
from app.schemas import ChatMessage, ToolResult


@dataclass(frozen=True)
class ToolDecision:
    use_time: bool = False
    use_weather: bool = False
    use_search: bool = False
    query: str = ""


TIME_KEYWORDS = (
    "今天",
    "现在",
    "当前",
    "几号",
    "日期",
    "星期",
    "几点",
    "时间",
    "today",
    "date",
    "time",
)
WEATHER_KEYWORDS = ("天气", "气温", "下雨", "温度", "weather")
KNOWN_WEATHER_CITIES = (
    "北京",
    "上海",
    "天津",
    "重庆",
    "广州",
    "深圳",
    "杭州",
    "成都",
    "武汉",
    "南京",
    "西安",
    "苏州",
    "郑州",
    "长沙",
    "青岛",
    "沈阳",
    "宁波",
    "昆明",
    "合肥",
    "佛山",
    "东莞",
    "福州",
    "厦门",
    "济南",
    "大连",
    "哈尔滨",
    "长春",
    "石家庄",
    "南宁",
    "南昌",
    "贵阳",
    "太原",
    "兰州",
    "海口",
    "乌鲁木齐",
    "呼和浩特",
    "银川",
    "西宁",
    "拉萨",
    "香港",
    "澳门",
    "台北",
)
SEARCH_KEYWORDS = (
    "新闻",
    "实时",
    "最新",
    "搜索",
    "查一下",
    "联网",
    "股价",
    "汇率",
    "价格",
    "news",
    "latest",
    "search",
)


def latest_user_text(messages: list[ChatMessage]) -> str:
    return next(
        (message.content for message in reversed(messages) if message.role == "user"),
        "",
    )


def decide_tools(messages: list[ChatMessage]) -> ToolDecision:
    """A lightweight keyword router for deciding which tools are useful."""
    query = latest_user_text(messages)
    lower_query = query.lower()
    use_time = any(keyword in lower_query for keyword in TIME_KEYWORDS)
    use_weather = any(keyword in lower_query for keyword in WEATHER_KEYWORDS)
    use_search = any(keyword in lower_query for keyword in SEARCH_KEYWORDS)
    return ToolDecision(
        use_time=use_time or use_weather or use_search,
        use_weather=use_weather,
        use_search=use_search,
        query=query,
    )


async def current_datetime_tool() -> ToolResult:
    """Return local Beijing time without any network dependency."""
    try:
        beijing_tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        # Windows virtualenvs may not include the IANA timezone database. UTC+8
        # is enough for Beijing time and keeps the realtime tool dependable.
        beijing_tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    now = datetime.now(beijing_tz)
    return ToolResult(
        name="current_datetime",
        title="当前日期时间",
        content=(
            f"当前北京时间：{now:%Y-%m-%d %H:%M:%S}，"
            f"星期{['一', '二', '三', '四', '五', '六', '日'][now.weekday()]}。"
        ),
    )


@tool("current_datetime")
async def current_datetime_langchain_tool() -> str:
    """获取当前北京时间。"""
    result = await current_datetime_tool()
    return result.content


async def weather_tool(query: str) -> ToolResult:
    """Fetch demo-friendly live weather from wttr.in."""
    city = extract_weather_city(query)

    url = f"https://wttr.in/{city}?format=j1&lang=zh"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return ToolResult(
            name="weather",
            title="实时天气",
            content=f"天气工具暂时不可用：{exc}",
            ok=False,
        )

    current = data.get("current_condition", [{}])[0]
    content = (
        f"{city}实时天气：{current.get('lang_zh', [{'value': ''}])[0].get('value', '')}，"
        f"气温 {current.get('temp_C', '?')}°C，"
        f"体感 {current.get('FeelsLikeC', '?')}°C，"
        f"湿度 {current.get('humidity', '?')}%。"
    )
    return ToolResult(name="weather", title="实时天气", content=content)


@tool("weather_lookup")
async def weather_langchain_tool(query: str) -> str:
    """根据用户问题查询城市实时天气。"""
    result = await weather_tool(query)
    return result.content


def extract_weather_city(query: str) -> str:
    """Extract a likely city name from Chinese weather questions.

    This is still lightweight, but it handles common forms such as:
    - 重庆今天天气怎么样
    - 今天重庆天气怎么样
    - 查一下重庆的气温
    """
    compact_query = re.sub(r"\s+", "", query)
    for candidate in KNOWN_WEATHER_CITIES:
        if candidate in compact_query:
            return candidate

    patterns = (
        r"(?:今天|明天|现在|当前|查一下|查询)?([\u4e00-\u9fa5]{2,8})(?:的)?(?:天气|气温|温度)",
        r"(?:天气|气温|温度).{0,3}?([\u4e00-\u9fa5]{2,8})",
    )
    stop_words = (
        "今天",
        "明天",
        "现在",
        "当前",
        "查询",
        "查一下",
        "请问",
        "一下",
        "怎么样",
    )
    for pattern in patterns:
        match = re.search(pattern, compact_query)
        if not match:
            continue
        city = match.group(1)
        for word in stop_words:
            city = city.replace(word, "")
        city = city.removesuffix("的")
        if len(city) >= 2:
            return city
    return "北京"


async def web_search_tool(query: str, settings: Settings) -> ToolResult:
    """Run web search with the first configured search provider."""
    if settings.tavily_api_key:
        return await tavily_search(query, settings.tavily_api_key)
    if settings.serpapi_api_key:
        return await serpapi_search(query, settings.serpapi_api_key)
    return ToolResult(
        name="web_search",
        title="联网搜索",
        content=(
            "联网搜索工具未配置。请在 .env 中配置 TAVILY_API_KEY 或 "
            "SERPAPI_API_KEY 后重启服务。"
        ),
        ok=False,
    )


async def tavily_search(query: str, api_key: str) -> ToolResult:
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return ToolResult(
            name="web_search",
            title="联网搜索",
            content=f"Tavily 搜索失败：{exc}",
            ok=False,
        )

    results = data.get("results", [])
    lines = [
        f"{index}. {item.get('title', '无标题')} - {item.get('content', '')} ({item.get('url', '')})"
        for index, item in enumerate(results, start=1)
    ]
    return ToolResult(
        name="web_search",
        title="联网搜索",
        content="\n".join(lines) if lines else "没有搜索到相关结果。",
    )


async def serpapi_search(query: str, api_key: str) -> ToolResult:
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(
                "https://serpapi.com/search.json",
                params={"q": query, "api_key": api_key, "engine": "google", "num": 5},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return ToolResult(
            name="web_search",
            title="联网搜索",
            content=f"SerpAPI 搜索失败：{exc}",
            ok=False,
        )

    results = data.get("organic_results", [])
    lines = [
        f"{index}. {item.get('title', '无标题')} - {item.get('snippet', '')} ({item.get('link', '')})"
        for index, item in enumerate(results, start=1)
    ]
    return ToolResult(
        name="web_search",
        title="联网搜索",
        content="\n".join(lines) if lines else "没有搜索到相关结果。",
    )


async def run_tools(messages: list[ChatMessage], settings: Settings) -> list[ToolResult]:
    """Execute all tools selected for the latest user question.

    Tools are exposed as LangChain tools, then invoked explicitly here. This
    keeps the current deterministic behavior while moving the tool layer onto
    LangChain primitives.
    """
    if not settings.enable_tools:
        return []

    decision = decide_tools(messages)
    results: list[ToolResult] = []
    if decision.use_time:
        content = await current_datetime_langchain_tool.ainvoke({})
        results.append(ToolResult(name="current_datetime", title="当前日期时间", content=content))
    if decision.use_weather:
        content = await weather_langchain_tool.ainvoke({"query": decision.query})
        results.append(ToolResult(name="weather", title="实时天气", content=content))
    if decision.use_search:
        results.append(await web_search_tool(decision.query, settings))
    return results


def augment_messages(messages: list[ChatMessage], tool_results: list[ToolResult]) -> list[ChatMessage]:
    """Prepend tool results as system context for the selected model."""
    if not tool_results:
        return messages

    tool_context = "\n".join(
        f"[{result.title} / {result.name} / {'成功' if result.ok else '失败'}]\n{result.content}"
        for result in tool_results
    )
    system = ChatMessage(
        role="system",
        content=(
            "你可以使用后端工具提供的实时信息回答用户。"
            "当工具结果与模型已有知识冲突时，优先相信工具结果。"
            "请自然地回答，不要声称自己无法访问当前时间或实时信息。\n\n"
            f"工具结果：\n{tool_context}"
        ),
    )
    return [system, *messages]
