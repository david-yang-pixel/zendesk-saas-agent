import pytest

from zd_agent.casework.adapters.language import public_reply_english_blockers
from zd_agent.casework.policies.reply_quality import public_reply_language_blockers


@pytest.mark.parametrize(
    "body",
    (
        "请提供错误信息。",
        "エラーの詳細を送ってください。",
        "오류 세부 정보를 보내 주세요.",
        "Пожалуйста, пришлите сведения об ошибке.",
        "يرجى إرسال تفاصيل الخطأ.",
    ),
)
def test_public_reply_blocks_non_english_scripts(body: str) -> None:
    assert public_reply_language_blockers(body)


def test_public_reply_allows_english_with_product_names_and_punctuation() -> None:
    body = (
        "Dify couldn't reproduce the issue. Please retry the API request and "
        "share the customer-visible error. Café is the workspace name."
    )

    assert public_reply_language_blockers(body) == ()


@pytest.mark.parametrize(
    "body",
    (
        "Confirmamos que su suscripción está activa.",
        "Votre abonnement est actif.",
        "O plano está ativo.",
        "Suscripción activa ahora.",
        "Abonnement actuellement actif.",
        "Plano está ativo.",
        "Problema no resuelto.",
        "Cuenta bloqueada.",
        "Compte bloqué.",
        "Pagamento recusado.",
        "Pago fallido.",
        "Paiement refusé.",
        "Solicitud completada.",
        "Plan activo.",
        "Falha na API.",
        "Service indisponible.",
        "Quota épuisé.",
        "Erro resolvido.",
        "Solicitud fallida.",
        "No hay error.",
        "Error en el plan.",
        "Error de API.",
        "API sin error.",
        "Problem im Plan.",
        "Jest problem.",
        "To jest problem.",
        "Jest error.",
        "Ga go na issue.",
        "Go na issue.",
        "Issue ke eng.",
        "Błąd nadal.",
    ),
)
def test_public_reply_blocks_non_english_latin_language(body: str) -> None:
    assert public_reply_english_blockers(body)


@pytest.mark.parametrize(
    "body",
    (
        "Request failed.",
        "Dify API quota.",
        "API quota exhausted.",
        "Quota has reset.",
        "Retry now.",
        "Request failed. Please retry the request.",
        "API quota exhausted. Please retry after the quota resets.",
        "Plan is active.",
        "Account is blocked.",
        "Please retry the request.",
        "The API request failed because the rate limit was reached.",
        "Your Café workspace is active on the Professional plan.",
    ),
)
def test_public_reply_allows_short_technical_english(body: str) -> None:
    assert public_reply_english_blockers(body) == ()
