class BaseSkill:
    name        = ""
    description = ""
    args_schema = {}

    def run(self, **kwargs):
        raise NotImplementedError

    def spec(self) -> dict:
        return {
            "name":        self.name,
            "description": self.description,
            "args":        self.args_schema,
        }
