from orbitkb.analysis.jvm_imports import parse_jvm_imports


def test_parses_a_java_import():
    source = "import software.amazon.awssdk.services.sqs.SqsClient;\n"

    assert parse_jvm_imports(source) == {"SqsClient": "software.amazon.awssdk.services.sqs.SqsClient"}


def test_parses_a_kotlin_import_without_semicolon():
    source = "import software.amazon.awssdk.services.sqs.SqsClient\n"

    assert parse_jvm_imports(source) == {"SqsClient": "software.amazon.awssdk.services.sqs.SqsClient"}


def test_parses_a_kotlin_aliased_import():
    source = "import software.amazon.awssdk.services.sqs.SqsClient as AwsSqs\n"

    assert parse_jvm_imports(source) == {"AwsSqs": "software.amazon.awssdk.services.sqs.SqsClient"}


def test_java_static_import_is_ignored():
    source = "import static java.util.Collections.emptyList;\n"

    assert parse_jvm_imports(source) == {}


def test_wildcard_import_cannot_resolve_a_single_fqn_and_is_ignored():
    source = "import com.amazonaws.services.sqs.*;\n"

    assert parse_jvm_imports(source) == {}


def test_multiple_imports_across_java_and_kotlin_style():
    source = (
        "import com.example.Foo;\n"
        "import com.example.Bar as B\n"
    )

    assert parse_jvm_imports(source) == {"Foo": "com.example.Foo", "B": "com.example.Bar"}


def test_no_imports_returns_empty_dict():
    assert parse_jvm_imports("class Foo {}\n") == {}
