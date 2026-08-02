"""验证 Profile 领域直接拥有模型和业务实现。"""


def test_profile_models_are_defined_by_profile_package() -> None:
    """画像值对象不应借由其他领域定义。"""
    from trowel_py.profile.models import (
        Profile,
        Suggestion,
    )

    assert Profile.__module__ == "trowel_py.profile.models"
    assert Suggestion.__module__ == "trowel_py.profile.models"


def test_profile_business_functions_are_owned_by_profile_package() -> None:
    """业务函数的定义模块应位于 Profile 领域。"""
    from trowel_py.profile.distill import run_daily_distill
    from trowel_py.profile.document import profile_to_body
    from trowel_py.profile.recalibration import plan_recalibration
    from trowel_py.profile.suggestions import load_suggestions

    functions = (
        profile_to_body,
        load_suggestions,
        run_daily_distill,
        plan_recalibration,
    )

    assert all(
        function.__module__.startswith("trowel_py.profile") for function in functions
    )
