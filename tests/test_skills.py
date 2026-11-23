"""The agent must expose exactly sixteen unique skills."""
from matlab_os.agent.skills import SKILLS, get_skills, register_skills


class FakeAgent:
    def __init__(self):
        self.registered = []

    def register_skill(self, name):
        self.registered.append(name)


def test_sixteen_unique_nonempty_skills():
    skills = get_skills()
    assert len(skills) == 16
    assert len(set(skills)) == 16
    assert all(isinstance(s, str) and s.strip() for s in skills)


def test_get_skills_returns_a_copy():
    first = get_skills()
    first.append("rogue")
    assert "rogue" not in get_skills()
    assert len(SKILLS) == 16


def test_register_skills_registers_every_skill_once_in_order():
    agent = FakeAgent()
    register_skills(agent)
    assert agent.registered == get_skills()