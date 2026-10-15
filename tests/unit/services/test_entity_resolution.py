from info_assistant.services.entity_resolution import EntityIdentity, EntityResolver


def test_same_name_with_same_company_and_location_can_merge() -> None:
    resolver = EntityResolver()
    candidate = EntityIdentity(name="Senior Engineer", organization="Acme", location="Seattle")
    existing = EntityIdentity(name="Senior Engineer", organization="Acme", location="Seattle")

    decision = resolver.match(candidate, [existing])

    assert decision.kind == "match"
    assert decision.matched_identity == existing


def test_same_title_at_different_companies_is_not_merged() -> None:
    resolver = EntityResolver()
    candidate = EntityIdentity(name="Engineer", organization="Acme")
    existing = EntityIdentity(name="Engineer", organization="Beta")

    decision = resolver.match(candidate, [existing])

    assert decision.kind == "distinct"


def test_same_name_without_identity_context_is_ambiguous() -> None:
    decision = EntityResolver().match(
        EntityIdentity(name="Mercury"), [EntityIdentity(name="Mercury")]
    )

    assert decision.kind == "ambiguous"
    assert "身份信息不足" in decision.reason
