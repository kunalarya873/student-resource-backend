from pydantic import BaseModel, ConfigDict


class CoreSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StandardMessageResponse(CoreSchema):
    message: str


class PaginationParams(BaseModel):
    page: int = 1
    limit: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit
