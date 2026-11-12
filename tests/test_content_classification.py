from investment_assistant.domain.news import ContentClassification, classify_content


def test_opinion_style_headline_is_not_labeled_as_fact() -> None:
    classification = classify_content(
        "SPCX",
        "finance.yahoo.com",
        "Prediction: SpaceX will make investors rich — here's why",
    )

    assert classification is ContentClassification.OPINION


def test_established_financial_reporting_can_be_factual() -> None:
    classification = classify_content(
        "SPCX",
        "reuters.com",
        "SpaceX files quarterly results with the SEC",
    )

    assert classification is ContentClassification.FACTUAL_FINANCIAL_REPORTING


def test_verified_ir_domain_is_always_an_official_release() -> None:
    classification = classify_content(
        "SPCX",
        "ir.spacex.com",
        "Here's why SpaceX announced a bond issuance",
    )

    assert classification is ContentClassification.OFFICIAL_COMPANY_RELEASE


def test_commentator_view_is_analyst_commentary_not_company_fact() -> None:
    classification = classify_content(
        "SPCX",
        "finance.yahoo.com",
        "Jim Cramer shares Morgan Stanley's view of SpaceX",
    )

    assert classification is ContentClassification.ANALYST_COMMENTARY


def test_implied_price_target_headline_is_opinion() -> None:
    classification = classify_content(
        "SPCX",
        "example.com",
        "SpaceX stock's $230 target implies a trillion-dollar gain",
    )

    assert classification is ContentClassification.OPINION
