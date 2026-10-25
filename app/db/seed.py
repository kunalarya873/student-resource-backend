import asyncio
from datetime import date

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.security import get_password_hash
from app.models.resource import AcademicYear, Subject
from app.models.storage import StorageQuota
from app.models.user import Department, Role, StudentSubject, TeacherSubject, User


async def seed_database() -> dict[str, int]:
    counts = {
        "roles": 0,
        "departments": 0,
        "subjects": 0,
        "academic_years": 0,
        "users": 0,
    }

    async with async_session_factory() as session, session.begin():
        # 1. Seed Roles
        roles_data = [
            {
                "role_id": 1,
                "name": "Administrator",
                "description": "Full system oversight and moderation",
            },
            {
                "role_id": 2,
                "name": "Teacher",
                "description": "Academic faculty and resource creator",
            },
            {
                "role_id": 3,
                "name": "Student",
                "description": "Enrolled student account",
            },
        ]
        for r in roles_data:
            existing_role = await session.scalar(select(Role).where(Role.role_id == r["role_id"]))
            if not existing_role:
                session.add(Role(**r))
                counts["roles"] += 1

        # 2. Seed Departments
        dept_data = [
            {"department_id": 1, "code": "CS", "name": "Computer Science"},
            {"department_id": 2, "code": "EE", "name": "Electrical Engineering"},
            {"department_id": 3, "code": "ME", "name": "Mechanical Engineering"},
        ]
        for d in dept_data:
            existing_dept = await session.scalar(
                select(Department).where(Department.department_id == d["department_id"])
            )
            if not existing_dept:
                session.add(Department(**d))
                counts["departments"] += 1

        # 3. Seed Academic Years
        years_data = [
            {
                "academic_year_id": 1,
                "label": "2025-2026",
                "start_date": date(2025, 8, 1),
                "end_date": date(2026, 5, 31),
            },
            {
                "academic_year_id": 2,
                "label": "2026-2027",
                "start_date": date(2026, 8, 1),
                "end_date": date(2027, 5, 31),
            },
        ]
        for y in years_data:
            existing_year = await session.scalar(
                select(AcademicYear).where(AcademicYear.academic_year_id == y["academic_year_id"])
            )
            if not existing_year:
                session.add(AcademicYear(**y))
                counts["academic_years"] += 1

        await session.flush()

        # 4. Seed Subjects
        subjects_data = [
            {
                "subject_id": 1,
                "department_id": 1,
                "code": "CS101",
                "name": "Introduction to Computer Programming",
                "semester": 1,
            },
            {
                "subject_id": 2,
                "department_id": 1,
                "code": "CS201",
                "name": "Data Structures and Algorithms",
                "semester": 3,
            },
            {
                "subject_id": 3,
                "department_id": 2,
                "code": "EE101",
                "name": "Basic Electrical Engineering",
                "semester": 1,
            },
        ]
        for s in subjects_data:
            existing_sub = await session.scalar(
                select(Subject).where(Subject.subject_id == s["subject_id"])
            )
            if not existing_sub:
                session.add(Subject(**s))
                counts["subjects"] += 1

        await session.flush()

        # 5. Seed Users & Quotas
        demo_password_hash = get_password_hash("amu_demo_2026!")

        users_data = [
            {
                "email": "admin@amu.edu",
                "full_name": "System Administrator",
                "role_id": 1,
                "department_id": None,
                "quota": settings.TEACHER_DEFAULT_QUOTA_BYTES,  # 15 GiB
            },
            {
                "email": "teacher@amu.edu",
                "full_name": "Dr. Tariq Ahmad",
                "role_id": 2,
                "department_id": 1,
                "quota": settings.TEACHER_DEFAULT_QUOTA_BYTES,  # 15 GiB
            },
            {
                "email": "student@amu.edu",
                "full_name": "Fatima Khan",
                "role_id": 3,
                "department_id": 1,
                "quota": settings.STUDENT_DEFAULT_QUOTA_BYTES,  # 1 GiB
            },
        ]

        created_users: dict[str, User] = {}
        for u in users_data:
            existing_user = await session.scalar(select(User).where(User.email == u["email"]))
            if not existing_user:
                user_obj = User(
                    email=u["email"],
                    full_name=u["full_name"],
                    password_hash=demo_password_hash,
                    role_id=u["role_id"],
                    department_id=u.get("department_id"),
                    status="ACTIVE",
                    consent_version="1.0",
                )
                session.add(user_obj)
                await session.flush()

                # Create 1:1 Storage Quota record
                session.add(
                    StorageQuota(
                        user_id=user_obj.user_id,
                        quota_bytes=u["quota"],
                        used_bytes=0,
                        reserved_bytes=0,
                    )
                )
                # pyrefly: ignore [unsupported-operation]
                created_users[u["email"]] = user_obj
                counts["users"] += 1
            else:
                # pyrefly: ignore [unsupported-operation]
                created_users[u["email"]] = existing_user

        await session.flush()

        # 6. Seed Student Subject Enrollment & Teacher Assignment
        student_user = created_users.get("student@amu.edu")
        teacher_user = created_users.get("teacher@amu.edu")

        if student_user:
            for sub_id in [1, 2]:
                enrollment = await session.scalar(
                    select(StudentSubject).where(
                        StudentSubject.student_id == student_user.user_id,
                        StudentSubject.subject_id == sub_id,
                    )
                )
                if not enrollment:
                    session.add(StudentSubject(student_id=student_user.user_id, subject_id=sub_id))

        if teacher_user:
            for sub_id in [1, 2]:
                assignment = await session.scalar(
                    select(TeacherSubject).where(
                        TeacherSubject.teacher_id == teacher_user.user_id,
                        TeacherSubject.subject_id == sub_id,
                    )
                )
                if not assignment:
                    session.add(TeacherSubject(teacher_id=teacher_user.user_id, subject_id=sub_id))

    print(f"Database seeding completed: {counts}")
    return counts


if __name__ == "__main__":
    asyncio.run(seed_database())
