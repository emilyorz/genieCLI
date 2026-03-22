from .base import Arg, BaseSkill
from ._registry import SkillRegistry

SkillRegistry.discover("skills")

ALL_SKILLS = SkillRegistry.all()
