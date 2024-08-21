from pydantic import BaseModel


class Secret(BaseModel):
    secret_key: str
    github_oauth_client_id: str
    github_oauth_client_secret: str
    @staticmethod
    def parse(**kwargs):
        # replace - with _ and . with _
        return Secret(**{k.replace("-", "_").replace(".", "_"): v for k, v in kwargs.items()})
