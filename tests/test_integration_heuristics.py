from blastmap.discovery.integration_heuristics import classify_target_kind


def test_known_vendor_names_are_classified_external():
    assert classify_target_kind("Stripe API", known_service_names=set()) == "external"
    assert classify_target_kind("stripe", known_service_names=set()) == "external"
    assert classify_target_kind("Twilio SMS", known_service_names=set()) == "external"
    assert classify_target_kind("AWS S3", known_service_names=set()) == "external"


def test_name_matching_project_naming_convention_is_classified_internal():
    known = {"checkout-service", "payments-service", "order-service"}
    assert classify_target_kind("shipping-service", known_service_names=known) == "internal"
    assert classify_target_kind("loyalty-service", known_service_names=known) == "internal"


def test_name_already_indexed_is_classified_internal():
    known = {"checkout-service", "payments-service"}
    assert classify_target_kind("payments-service", known_service_names=known) == "internal"


def test_unrecognizable_name_is_unknown():
    known = {"checkout-service", "payments-service"}
    assert classify_target_kind("some_random_thing_xyz", known_service_names=known) == "unknown"
    assert classify_target_kind("", known_service_names=known) == "unknown"


def test_vendor_match_takes_precedence_over_naming_convention():
    # A vendor name that happens to also look like it could match a naming
    # convention should still be flagged external — vendor keywords are a stronger,
    # more specific signal than the generic suffix heuristic.
    known = {"payment-service"}
    assert classify_target_kind("stripe-service", known_service_names=known) == "external"
