"""Session-local topic resolution. Conversation is context, never factual evidence."""
import re

MAX_MESSAGES = 24
MAX_HISTORY_CHARS = 8000


def remember(state, query, answer):
    history = state["history"] + [
        {"role": "user", "content": query},
        {"role": "assistant", "content": answer},
    ]
    history = history[-MAX_MESSAGES:]
    while len(history) > 2 and sum(len(m["content"]) for m in history) > MAX_HISTORY_CHARS:
        history = history[2:]
    state["history"] = history


def resolve(query, topic_ids, index, persona):
    """Return focused source ids and optional clarification. Empty ids mean new search."""
    explicit = index.explicit_projects(query)
    projects = index.projects
    project_names = ", ".join(index.by_id[i]["title"] for i in projects)
    owner_pattern = "|".join(re.escape(name) for name in [persona.name, persona.english_name, *persona.aliases])
    if explicit:
        return explicit, None
    prior = [i for i in topic_ids if i in index.by_id]
    # Explicit non-project subjects must override a previous project.
    if re.search(r"경력|학력|졸업|연락|이메일|채용|career|education|contact|email", query, re.I):
        return [], None
    if re.search(rf"({owner_pattern}|개인|전체|전반).*(기술|스택|역량)|자기소개|일하는 방식", query):
        return [], None
    if re.search(r"다른|나머지|other|remaining", query, re.I) and any(i in projects for i in prior):
        others = [i for i in projects if i not in prior]
        return (others, None) if others else ([], f"프로젝트를 모두 살펴봤어요. {project_names} 중 어떤 내용을 더 알아볼까요?")
    ordinal = re.search(r"(첫\s*번째|두\s*번째|세\s*번째|first|second|third)", query, re.I)
    if ordinal and prior:
        word = re.sub(r"\s", "", ordinal.group()).lower()
        index = {"첫번째": 0, "first": 0, "두번째": 1, "second": 1, "세번째": 2, "third": 2}[word]
        if index < len(prior):
            return [prior[index]], None
        return [], "앞서 안내한 항목 중 어떤 것을 뜻하나요? 이름을 알려 주세요."
    followup = bool(re.search(
        r"그(?:거|것|중|건|걸| 프로젝트)|해당|이 프로젝트|방금|아까|이전|더 자세|자세히|구체적|"
        r"짧게|요약|다시 설명|영어로|한국어로|비교|차이|어떤 기술|사용한 기술|기술 스택|"
        r"어떻게|왜|역할|장점|단점|어려|개선|성과|기간|언제|what about|tell me more|"
        r"\bit\b|\bthat\b|\bthose\b|\bcompare\b|\bsummarize\b|\bstack\b|\bwhy\b",
        query, re.I,
    ))
    if followup:
        if not prior:
            if re.search(r"그거|그것|그 프로젝트|해당|더 자세|자세히|\bit\b|\bthat\b|tell me more", query, re.I):
                return [], "어떤 프로젝트나 내용을 말씀하시는지 이름을 알려 주세요."
            return [], None
        if len(prior) > 1 and re.search(r"그 프로젝트|그거|그것|\bthat project\b|\bit\b", query, re.I) and not re.search(r"비교|모두|각각|compare", query, re.I):
            names = ", ".join(index.by_id[i]["title"] for i in prior)
            return [], f"앞서 {names}를 함께 살펴봤어요. 어떤 프로젝트를 뜻하나요?"
        return prior, None
    return [], None
