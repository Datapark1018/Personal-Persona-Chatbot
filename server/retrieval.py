"""Small, deterministic BM25 index over reviewed public demo documents."""
import math
import re
from collections import Counter

def tokens(text):
    words = re.findall(r"[a-z0-9]+|[가-힣]+", text.lower())
    # Korean bigrams help inflected words without an extra morphology dependency.
    return words + [w[i:i+2] for w in words if re.fullmatch(r"[가-힣]{3,}", w) for i in range(len(w)-1)]


class KnowledgeIndex:
    def __init__(self, persona):
        self.documents = persona.public_documents()
        self.by_id = {doc["id"]: doc for doc in self.documents}
        self.projects = [doc["id"] for doc in self.documents if doc["category"] == "project"]
        self.aliases = {doc["id"]: [doc["id"], doc["title"], *doc["aliases"]] for doc in self.documents if doc["category"] == "project"}
        self.counts = [Counter(tokens(d["text"] + " " + " ".join(d["tags"] + d["aliases"]))) for d in self.documents]
        self.average = sum(sum(c.values()) for c in self.counts) / len(self.counts) or 1

    def explicit_projects(self, query):
        # Sort by appearance, so "second" follows the user's ordering.
        found = []
        for key, aliases in self.aliases.items():
            positions = []
            for alias in aliases:
                pattern = re.escape(alias)
                if alias[0].isascii() and alias[0].isalnum():
                    pattern = r"(?<![a-z0-9])" + pattern
                if alias[-1].isascii() and alias[-1].isalnum():
                    pattern += r"(?![a-z0-9])"
                match = re.search(pattern, query, re.I)
                if match:
                    positions.append(match.start())
            if positions:
                found.append((min(positions), key))
        return [key for _, key in sorted(found)]

    def retrieve(self, query):
        terms = set(tokens(query))
        overview = bool(re.search(r"프로젝트|projects", query, re.I)) and not self.explicit_projects(query)
        ranked = []
        for doc, counts in zip(self.documents, self.counts):
            if overview and doc["category"] != "project":
                continue
            score = 0.0
            for term in terms:
                freq = counts[term]
                df = sum(term in c for c in self.counts)
                idf = math.log(1 + (len(self.counts) - df + .5) / (df + .5))
                score += idf * freq * 2.5 / (freq + 1.5 * (.25 + .75 * sum(counts.values()) / self.average))
            score += 3 * sum(tag.casefold() in query.casefold() for tag in doc["tags"])
            if score > 1.0:
                ranked.append((score, doc))
        return [d for _, d in sorted(ranked, key=lambda x: -x[0])[:3]]
