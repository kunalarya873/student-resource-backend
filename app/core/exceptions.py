from fastapi import HTTPException, status


class DomainException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code=status_code, detail=detail)


class QuotaExceededException(DomainException):
    def __init__(self, available_bytes: int, requested_bytes: int):
        super().__init__(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Storage quota exceeded. Available: {available_bytes} bytes, "
                f"Requested: {requested_bytes} bytes."
            ),
        )


class FolderNotEmptyException(DomainException):
    def __init__(self, folder_id: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Folder {folder_id} is not empty. Remove subfolders and resources before deleting.",
        )


class FolderCycleException(DomainException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot move folder into itself or one of its descendants (circular hierarchy).",
        )


class PermissionDeniedException(DomainException):
    def __init__(self, message: str = "Permission denied for this operation"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=message)
