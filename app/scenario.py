"""Scenario preset for turning the generic assistant into a service desk app."""

from app.schemas import ChatMessage


SCENARIO_OPTIONS = {
    "id": "campus_service_desk",
    "name": "校园服务智能助手",
    "subtitle": "面向校园办事咨询、宿舍报修、图片故障描述和实时信息查询。",
    "badges": ["办事咨询", "报修登记", "图片问答", "实时工具"],
    "quick_actions": [
        {
            "label": "宿舍报修",
            "prompt": "宿舍空调不制冷，应该怎么报修？需要准备哪些信息？",
        },
        {
            "label": "上传故障图片",
            "prompt": "我上传了一张故障图片，请帮我判断问题并生成报修描述。",
        },
        {
            "label": "办事材料",
            "prompt": "学生证补办需要哪些材料和办理步骤？",
        },
        {
            "label": "天气出行",
            "prompt": "今天学校附近天气怎么样，适合户外活动吗？",
        },
    ],
}


SCENARIO_SYSTEM_PROMPT = """你是校园服务智能助手，服务对象是学生、老师和校园后勤人员。
你的任务不是泛泛聊天，而是把用户的问题转化为清晰、可执行的校园服务建议。

工作原则：
1. 优先识别用户意图：办事咨询、宿舍/设备报修、图片故障描述、天气出行、资料问答或普通问答。
2. 涉及报修时，主动整理地点、设备、故障现象、紧急程度、联系方式等字段；缺少关键信息时用简短问题追问。
3. 涉及上传图片时，结合图片和用户文字生成问题判断、报修描述和下一步建议；如果当前模型不能真正识图，要诚实说明。
4. 涉及日期、时间、天气、新闻或实时信息时，优先使用工具结果；不要编造实时数据。
5. 回复保持中文、简洁、服务台口吻，给出步骤、清单或可复制的报修文本。
"""


def get_scenario_options() -> dict[str, object]:
    """Return serializable scenario metadata for the frontend."""
    return SCENARIO_OPTIONS


def apply_scenario_prompt(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Prepend the domain prompt while preserving the user's conversation."""
    return [
        ChatMessage(role="system", content=SCENARIO_SYSTEM_PROMPT),
        *messages,
    ]
