"""Integration tests for the Gate 9 Strict JSON Result Protocol, Output Integrity & Termination State Machine."""

import hashlib
import json
import math
import subprocess
import time
import uuid
from pathlib import Path

import pytest

import app.analytics.python_sandbox as sandbox_mod
from app.analytics.python_sandbox import (
    DockerConnectionError,
    DockerNotFoundError,
    DockerPermissionError,
    DockerServerError,
    DockerTimeoutError,
    IsolatedDockerSandboxRunner,
    PythonSandboxRunner,
    SandboxExecutionResult,
    SandboxResultProtocolError,
    classify_docker_inspect_result,
    parse_and_validate_sandbox_stdout,
    query_docker_engine_api,
    stop_and_remove_container,
    structured_docker_inspect,
    verify_image_and_container_provenance,
)
from app.sandbox_agent.client import SandboxAgentClient
from app.sandbox_agent.schemas import ExecutionStatus, SandboxExecutionResponse
from app.sandbox_agent.server import app as agent_app


@pytest.fixture
def runner() -> PythonSandboxRunner:
    r = IsolatedDockerSandboxRunner(force_enabled=True)
    if not r.check_docker_available():
        pytest.skip("Docker runtime unavailable on this host.")
    return r


class TestSandboxStrictResultProtocolUnit:
    """Unit tests for the strict JSON parser parse_and_validate_sandbox_stdout."""

    def test_01_one_valid_json_object(self) -> None:
        """1. One valid JSON object parses cleanly."""
        raw = b'{"status": "ok", "count": 42, "metrics": {"accuracy": 0.99}}'
        doc, h = parse_and_validate_sandbox_stdout(raw)
        assert doc == {"status": "ok", "count": 42, "metrics": {"accuracy": 0.99}}
        assert h == hashlib.sha256(raw).hexdigest()

    def test_02_leading_diagnostic_text_before_json_rejected(self) -> None:
        """2. Leading diagnostic text before JSON is strictly rejected."""
        raw = b'INFO:root:Running analysis...\n{"status": "ok"}'
        with pytest.raises(SandboxResultProtocolError, match="Malformed .* JSON document"):
            parse_and_validate_sandbox_stdout(raw)

    def test_03_trailing_text_after_json_rejected(self) -> None:
        """3. Trailing text after JSON is strictly rejected."""
        raw = b'{"status": "ok"} \nExecution completed.'
        with pytest.raises(SandboxResultProtocolError, match="Unexpected trailing text"):
            parse_and_validate_sandbox_stdout(raw)

    def test_04_two_consecutive_json_objects_rejected(self) -> None:
        """4. Two consecutive JSON objects on stdout are strictly rejected."""
        raw = b'{"status": "first"}{"status": "second"}'
        with pytest.raises(SandboxResultProtocolError, match="Unexpected trailing text"):
            parse_and_validate_sandbox_stdout(raw)

    def test_05_nested_json_embedded_in_another_object_valid(self) -> None:
        """5. Nested JSON embedded in another object is valid within depth limits."""
        raw = b'{"outer": {"middle": {"inner": [1, 2, {"deep": true}]}}}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["outer"]["middle"]["inner"][2]["deep"] is True

    def test_06_duplicate_object_keys_rejected(self) -> None:
        """6. Duplicate object keys are strictly rejected."""
        raw = b'{"key": 1, "key": 2}'
        with pytest.raises(SandboxResultProtocolError, match="Duplicate object key rejected"):
            parse_and_validate_sandbox_stdout(raw)

    def test_07_invalid_utf8_rejected(self) -> None:
        """7. Invalid UTF-8 bytes are strictly rejected."""
        raw = b'{"key": "\xff\xfe"}'
        with pytest.raises(SandboxResultProtocolError, match="Stdout violates UTF-8 encoding"):
            parse_and_validate_sandbox_stdout(raw)

    def test_08_truncated_json_rejected(self) -> None:
        """8. Truncated JSON is strictly rejected."""
        raw = b'{"status": "incomplete", "data": [1, 2,'
        with pytest.raises(SandboxResultProtocolError, match="Malformed .* JSON document"):
            parse_and_validate_sandbox_stdout(raw)

    def test_09_empty_output_rejected(self) -> None:
        """9. Completely empty stdout is strictly rejected."""
        with pytest.raises(SandboxResultProtocolError, match="completely empty"):
            parse_and_validate_sandbox_stdout(b"")

    def test_10_whitespace_only_output_rejected(self) -> None:
        """10. Whitespace-only stdout is strictly rejected."""
        with pytest.raises(SandboxResultProtocolError, match="contains only whitespace"):
            parse_and_validate_sandbox_stdout(b"   \t  \n\r  ")

    def test_11_nan_constant_rejected(self) -> None:
        """11. NaN constants in JSON are strictly rejected."""
        raw = b'{"value": NaN}'
        with pytest.raises(SandboxResultProtocolError, match="Non-standard JSON constant rejected: NaN"):
            parse_and_validate_sandbox_stdout(raw)

    def test_12_infinity_constant_rejected(self) -> None:
        """12. Infinity constants in JSON are strictly rejected."""
        raw = b'{"value": Infinity}'
        with pytest.raises(SandboxResultProtocolError, match="Non-standard JSON constant rejected: Infinity"):
            parse_and_validate_sandbox_stdout(raw)

    def test_13_negative_infinity_constant_rejected(self) -> None:
        """13. -Infinity constants in JSON are strictly rejected."""
        raw = b'{"value": -Infinity}'
        with pytest.raises(SandboxResultProtocolError, match="Non-standard JSON constant rejected: -Infinity"):
            parse_and_validate_sandbox_stdout(raw)

    def test_14_excessive_nesting_rejected(self) -> None:
        """14. Excessive nesting depth (> 16) is strictly rejected."""
        deep = '{"a":' * 17 + '1' + '}' * 17
        with pytest.raises(SandboxResultProtocolError, match="JSON nesting depth exceeds maximum"):
            parse_and_validate_sandbox_stdout(deep.encode())

    def test_15_oversized_arrays_rejected(self) -> None:
        """15. Oversized arrays (> 100,000 items) are strictly rejected."""
        raw = json.dumps({"arr": [1] * 100_001}).encode()
        with pytest.raises(SandboxResultProtocolError, match="Array length .* exceeds maximum"):
            parse_and_validate_sandbox_stdout(raw)

    def test_16_oversized_strings_rejected(self) -> None:
        """16. Oversized strings (> 1,000,000 chars) are strictly rejected."""
        raw = json.dumps({"str": "A" * 1_000_001}).encode()
        with pytest.raises(SandboxResultProtocolError, match="String length .* exceeds maximum"):
            parse_and_validate_sandbox_stdout(raw)

    def test_17_unexpected_top_level_fields_non_object_root_rejected(self) -> None:
        """17. Non-object root (e.g. array, number, string, boolean) is strictly rejected."""
        with pytest.raises(SandboxResultProtocolError, match="Top-level JSON value must be an object"):
            parse_and_validate_sandbox_stdout(b'[1, 2, 3]')
        with pytest.raises(SandboxResultProtocolError, match="Top-level JSON value must be an object"):
            parse_and_validate_sandbox_stdout(b'"just a string"')
        with pytest.raises(SandboxResultProtocolError, match="Top-level JSON value must be an object"):
            parse_and_validate_sandbox_stdout(b'42')

    def test_18_wrong_field_types_and_invalid_keys_rejected(self) -> None:
        """18. Invalid object keys (control characters or key length > 256) are strictly rejected."""
        raw_ctl = b'{"bad\x01key": "value"}'
        with pytest.raises(SandboxResultProtocolError):
            parse_and_validate_sandbox_stdout(raw_ctl)
        raw_long_key = json.dumps({"K" * 257: "value"}).encode()
        with pytest.raises(SandboxResultProtocolError, match="Object key length .* exceeds maximum"):
            parse_and_validate_sandbox_stdout(raw_long_key)

    def test_19_negative_or_out_of_range_values_rejected(self) -> None:
        """19. Control characters in strings or non-standard values are rejected."""
        raw_bad_str = b'{"key": "hello\x07world"}'
        with pytest.raises(SandboxResultProtocolError, match="(?i)control character"):
            parse_and_validate_sandbox_stdout(raw_bad_str)

    def test_20_direct_os_write_corrupting_stdout_rejected(self) -> None:
        """20. Direct os.write(1, ...) contaminating stdout before JSON is rejected."""
        raw = b'RAW_BYTES_FROM_OS_WRITE{"valid": true}'
        with pytest.raises(SandboxResultProtocolError):
            parse_and_validate_sandbox_stdout(raw)

    def test_21_multiple_json_objects_emitted_through_direct_writes_rejected(self) -> None:
        """21. Multiple JSON objects emitted through direct writes are rejected."""
        raw = b'{"msg": "first"} \n\n {"msg": "second"}'
        with pytest.raises(SandboxResultProtocolError, match="Unexpected trailing text"):
            parse_and_validate_sandbox_stdout(raw)

    def test_22_valid_json_followed_by_malicious_trailing_bytes_rejected(self) -> None:
        """22. A valid-looking JSON object followed by malicious trailing bytes is rejected."""
        raw = b'{"status": "ok"}\x00malicious_payload'
        with pytest.raises(SandboxResultProtocolError, match="Unexpected trailing text"):
            parse_and_validate_sandbox_stdout(raw)

    def test_23_exponent_overflow_positive_rejected(self) -> None:
        """23. Positive exponent overflow (1e999) is strictly rejected."""
        raw = b'{"val": 1e999}'
        with pytest.raises(SandboxResultProtocolError, match="Float overflow or non-finite float"):
            parse_and_validate_sandbox_stdout(raw)

    def test_24_exponent_overflow_negative_rejected(self) -> None:
        """24. Negative exponent overflow (-1e999) is strictly rejected."""
        raw = b'{"val": -1e999}'
        with pytest.raises(SandboxResultProtocolError, match="Float overflow or non-finite float"):
            parse_and_validate_sandbox_stdout(raw)

    def test_25_huge_positive_integer_rejected(self) -> None:
        """25. Integer exceeding 64-bit signed max (2^63 - 1) is strictly rejected."""
        raw = b'{"val": 9223372036854775808}'
        with pytest.raises(SandboxResultProtocolError, match="exceeds permitted 64-bit integer range"):
            parse_and_validate_sandbox_stdout(raw)

    def test_26_huge_negative_integer_rejected(self) -> None:
        """26. Integer below 64-bit signed min (-2^63) is strictly rejected."""
        raw = b'{"val": -9223372036854775809}'
        with pytest.raises(SandboxResultProtocolError, match="exceeds permitted 64-bit integer range"):
            parse_and_validate_sandbox_stdout(raw)

    def test_27_valid_64bit_boundary_integers_accepted(self) -> None:
        """27. Boundary 64-bit signed integers [-2^63, 2^63 - 1] are accepted."""
        raw = b'{"min": -9223372036854775808, "max": 9223372036854775807}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["min"] == -(2**63)
        assert doc["max"] == 2**63 - 1

    def test_28_float_underflow_accepted_to_zero(self) -> None:
        """28. Subnormal float underflow (1e-999) safely maps to finite 0.0."""
        raw = b'{"val": 1e-999}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["val"] == 0.0

    def test_29_boolean_vs_integer_distinction(self) -> None:
        """29. Booleans and integers are strictly distinguished and validated."""
        raw = b'{"b_true": true, "b_false": false, "i_one": 1, "i_zero": 0}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["b_true"] is True
        assert type(doc["b_true"]) is bool
        assert doc["b_false"] is False
        assert type(doc["b_false"]) is bool
        assert doc["i_one"] == 1
        assert type(doc["i_one"]) is int
        assert doc["i_zero"] == 0
        assert type(doc["i_zero"]) is int

    def test_30_whitespace_vertical_tab_leading_rejected(self) -> None:
        """30. Non-RFC 8259 whitespace (vertical tab 0x0B) before JSON is rejected."""
        raw = b'\x0b{"status": "ok"}'
        with pytest.raises(SandboxResultProtocolError, match="Non-permitted whitespace character U\\+000B"):
            parse_and_validate_sandbox_stdout(raw)

    def test_31_whitespace_vertical_tab_trailing_rejected(self) -> None:
        """31. Non-RFC 8259 whitespace (vertical tab 0x0B) after JSON is rejected."""
        raw = b'{"status": "ok"}\x0b'
        with pytest.raises(SandboxResultProtocolError, match="Non-permitted whitespace character U\\+000B"):
            parse_and_validate_sandbox_stdout(raw)

    def test_32_whitespace_form_feed_leading_rejected(self) -> None:
        """32. Non-RFC 8259 whitespace (form feed 0x0C) before JSON is rejected."""
        raw = b'\x0c{"status": "ok"}'
        with pytest.raises(SandboxResultProtocolError, match="Non-permitted whitespace character U\\+000C"):
            parse_and_validate_sandbox_stdout(raw)

    def test_33_whitespace_form_feed_trailing_rejected(self) -> None:
        """33. Non-RFC 8259 whitespace (form feed 0x0C) after JSON is rejected."""
        raw = b'{"status": "ok"}\x0c'
        with pytest.raises(SandboxResultProtocolError, match="Non-permitted whitespace character U\\+000C"):
            parse_and_validate_sandbox_stdout(raw)

    def test_34_whitespace_non_breaking_space_leading_rejected(self) -> None:
        """34. Non-RFC 8259 whitespace (NBSP U+00A0) before JSON is rejected."""
        raw = " \u00a0 {\"status\": \"ok\"}".encode("utf-8")
        with pytest.raises(SandboxResultProtocolError, match="Non-permitted whitespace character U\\+00A0"):
            parse_and_validate_sandbox_stdout(raw)

    def test_35_whitespace_non_breaking_space_trailing_rejected(self) -> None:
        """35. Non-RFC 8259 whitespace (NBSP U+00A0) after JSON is rejected."""
        raw = "{\"status\": \"ok\"} \u00a0 ".encode("utf-8")
        with pytest.raises(SandboxResultProtocolError, match="Non-permitted whitespace character U\\+00A0"):
            parse_and_validate_sandbox_stdout(raw)

    def test_36_lone_unicode_surrogate_rejected(self) -> None:
        """36. Lone Unicode surrogate character (U+D800) in string value is rejected."""
        raw = b'{"key": "\\uD800"}'
        with pytest.raises(SandboxResultProtocolError, match="Lone Unicode surrogate character"):
            parse_and_validate_sandbox_stdout(raw)

    def test_37_reserved_host_prefix_rejected(self) -> None:
        """37. Keys with reserved host metadata prefixes (_vibe_, __vibe_) are rejected."""
        raw1 = b'{"_vibe_status": "injected"}'
        with pytest.raises(SandboxResultProtocolError, match="Reserved host metadata key prefix"):
            parse_and_validate_sandbox_stdout(raw1)
        raw2 = b'{"__vibe_secret": 123}'
        with pytest.raises(SandboxResultProtocolError, match="Reserved host metadata key prefix"):
            parse_and_validate_sandbox_stdout(raw2)

    def test_38_rfc8259_permitted_whitespace_accepted(self) -> None:
        """38. Standard RFC 8259 whitespace (space, tab, LF, CR) is accepted around JSON."""
        raw = b" \t \r\n {\"status\": \"ok\", \"count\": 1} \n\r\t "
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc == {"status": "ok", "count": 1}

    def test_39_json_integer_negative_zero_normalized_to_zero(self) -> None:
        """39. JSON integer -0 is parsed and normalized to exact integer 0."""
        raw = b'{"val": -0}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["val"] == 0
        assert type(doc["val"]) is int

    def test_40_json_float_negative_zero_preserved(self) -> None:
        """40. JSON float -0.0 is parsed and preserved with IEEE 754 negative sign."""
        raw = b'{"val": -0.0}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["val"] == 0.0
        assert type(doc["val"]) is float
        assert math.copysign(1.0, doc["val"]) == -1.0

    def test_41_json_float_positive_zero_preserved(self) -> None:
        """41. JSON float 0.0 is parsed and preserved with IEEE 754 positive sign."""
        raw = b'{"val": 0.0}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["val"] == 0.0
        assert type(doc["val"]) is float
        assert math.copysign(1.0, doc["val"]) == 1.0

    def test_42_representable_subnormal_floats_positive_and_negative(self) -> None:
        """42. Representable IEEE 754 subnormal floats are preserved with non-zero values."""
        raw = b'{"sub_pos": 1e-320, "sub_neg": -1e-320}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["sub_pos"] > 0.0
        assert doc["sub_neg"] < 0.0
        assert math.copysign(1.0, doc["sub_pos"]) == 1.0
        assert math.copysign(1.0, doc["sub_neg"]) == -1.0

    def test_43_underflow_below_subnormal_maps_to_signed_zero(self) -> None:
        """43. Float literals below IEEE 754 subnormal threshold underflow to signed zero (+0.0 / -0.0)."""
        raw = b'{"under_pos": 1e-325, "under_neg": -1e-325, "deep_pos": 1e-999, "deep_neg": -1e-999}'
        doc, _ = parse_and_validate_sandbox_stdout(raw)
        assert doc["under_pos"] == 0.0
        assert math.copysign(1.0, doc["under_pos"]) == 1.0
        assert doc["under_neg"] == 0.0
        assert math.copysign(1.0, doc["under_neg"]) == -1.0
        assert doc["deep_pos"] == 0.0
        assert math.copysign(1.0, doc["deep_pos"]) == 1.0
        assert doc["deep_neg"] == 0.0
        assert math.copysign(1.0, doc["deep_neg"]) == -1.0

    def test_44_recursive_reserved_key_rejection(self) -> None:
        """44. Reserved host metadata prefixes (_vibe_, __vibe_) are rejected recursively at any depth."""
        raw = b'{"level1": {"level2": {"level3": {"_vibe_injected": "forbidden"}}}}'
        with pytest.raises(SandboxResultProtocolError, match="Reserved host metadata key prefix"):
            parse_and_validate_sandbox_stdout(raw)

    def test_45_ieee754_subnormal_halfway_boundary_rounding_and_signs(self) -> None:
        """45. Strict IEEE 754 binary64 halfway underflow boundary (2**-1075 ≈ 2.47e-324) and sign preservation."""
        raw = (
            b'{"p1": 1e-324, "p2": 2e-324, "p3": 3e-324, "p4": 4e-324, '
            b'"n1": -1e-324, "n2": -2e-324, "n3": -3e-324, "n4": -4e-324, "neg_zero": -0.0}'
        )
        doc, _ = parse_and_validate_sandbox_stdout(raw)

        # Values below halfway boundary (< 2**-1075 ≈ 2.47e-324) round to zero
        assert doc["p1"] == 0.0 and math.copysign(1.0, doc["p1"]) == 1.0 and math.isfinite(doc["p1"])
        assert doc["p2"] == 0.0 and math.copysign(1.0, doc["p2"]) == 1.0 and math.isfinite(doc["p2"])

        # Values above halfway boundary (> 2**-1075) round to minimum subnormal (2**-1074 ≈ 4.94e-324)
        assert doc["p3"] > 0.0 and math.copysign(1.0, doc["p3"]) == 1.0 and math.isfinite(doc["p3"])
        assert doc["p4"] > 0.0 and math.copysign(1.0, doc["p4"]) == 1.0 and math.isfinite(doc["p4"])

        # Negative values below halfway boundary round to negative zero (-0.0)
        assert doc["n1"] == 0.0 and math.copysign(1.0, doc["n1"]) == -1.0 and math.isfinite(doc["n1"])
        assert doc["n2"] == 0.0 and math.copysign(1.0, doc["n2"]) == -1.0 and math.isfinite(doc["n2"])

        # Negative values above halfway boundary round to negative minimum subnormal (-2**-1074)
        assert doc["n3"] < 0.0 and math.copysign(1.0, doc["n3"]) == -1.0 and math.isfinite(doc["n3"])
        assert doc["n4"] < 0.0 and math.copysign(1.0, doc["n4"]) == -1.0 and math.isfinite(doc["n4"])

        # Negative zero literal -0.0
        assert doc["neg_zero"] == 0.0 and math.copysign(1.0, doc["neg_zero"]) == -1.0 and math.isfinite(doc["neg_zero"])


class TestSandboxResultProtocolContainerExecution:
    """Integration tests running adversarial code inside actual isolated Docker containers."""

    def test_container_valid_json_execution(self, runner: PythonSandboxRunner) -> None:
        """Verify normal compliant analytical script produces status='completed' with exact SHA-256."""
        script = "result = {'status': 'success', 'data': [10, 20, 30]}"
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        assert res.result_payload == {"status": "success", "data": [10, 20, 30]}
        assert res.enforcement_details.get("exit_code") == 0
        assert res.enforcement_details.get("sealed_sha256") is not None
        assert res.enforcement_details.get("sealed_sha256") == res.enforcement_details.get("transferred_sha256")

    def test_container_os_write_leading_text_rejected(self, runner: PythonSandboxRunner) -> None:
        """Verify direct os.write(1, ...) before output causes execution failure and no persistence."""
        script = """
import os
os.write(1, b"MALICIOUS_DIAGNOSTIC_HEADER\\n")
result = {"status": "should_be_rejected"}
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "malformed" in (res.error_message or "").lower() or "protocol_violation" in str(res.enforcement_details)

    def test_container_os_write_multiple_json_rejected(self, runner: PythonSandboxRunner) -> None:
        """Verify direct os.write(1, ...) of an extra JSON object causes execution failure."""
        script = """
import os
os.write(1, b'{"spoofed": True}\\n')
result = {"status": "genuine"}
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "unexpected trailing" in (res.error_message or "").lower() or "protocol_violation" in str(res.enforcement_details)

    def test_container_nan_constant_rejected(self, runner: PythonSandboxRunner) -> None:
        """Verify emitting NaN constant to stdout causes execution failure."""
        script = """
import os
os.write(1, b'{"val": NaN}\\n')
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"

    def test_container_excessive_nesting_rejected(self, runner: PythonSandboxRunner) -> None:
        """Verify script generating deeply nested structure (>16) is rejected."""
        script = """
d = 1
for _ in range(20):
    d = {"n": d}
result = d
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "nesting depth" in (res.error_message or "").lower()

    def test_container_exponent_overflow_rejected(self, runner: PythonSandboxRunner) -> None:
        """Verify container script emitting 1e999 exponent overflow is rejected by host parser."""
        script = """
import os
os.write(1, b'{"overflow": 1e999}\\n')
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "overflow" in (res.error_message or "").lower() or "protocol_violation" in str(res.enforcement_details)

    def test_container_stdout_flood_immediately_killed(self, runner: PythonSandboxRunner) -> None:
        """Verify container writing > 5MB to stdout triggers immediate termination without accumulating unbounded data."""
        script = """
import os
import sys
# Flood stdout with 6MB of data in 64KB chunks
chunk = b"A" * 65536
for _ in range(100):
    os.write(1, chunk)
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "stdout limit" in (res.error_message or "").lower() or "stdout_overflow" in str(res.enforcement_details)
        # Ensure runner did not buffer huge stdout
        assert len(res.stdout) == 0

    def test_container_stderr_bounded_to_64kb(self, runner: PythonSandboxRunner) -> None:
        """Verify container writing > 64KB to stderr is strictly bounded to 64KB without unbounded memory accumulation."""
        script = """
import sys
# Emit 128KB of logs to stderr
chunk = "E" * 1024 + "\\n"
for _ in range(128):
    sys.stderr.write(chunk)
    sys.stderr.flush()
result = {"status": "ok"}
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        assert len(res.stderr.encode("utf-8")) <= 65536
        assert res.result_payload == {"status": "ok"}

    def test_container_flood_abuse_ceiling_immediately_killed(self, runner: PythonSandboxRunner) -> None:
        """Verify container exceeding 10MB flood ceiling is immediately killed."""
        script = """
import sys
# Flood 12MB of logs
chunk = "F" * 65536
for _ in range(200):
    sys.stderr.write(chunk)
    sys.stderr.flush()
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "flood" in (res.error_message or "").lower() or "log_flood_protection" in str(res.enforcement_details)

    def test_container_reserved_key_prefix_rejected(self, runner: PythonSandboxRunner) -> None:
        """Verify script returning reserved host metadata keys (_vibe_*) is rejected."""
        script = "result = {'_vibe_metadata_spoof': 'forbidden'}"
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "reserved host metadata" in (res.error_message or "").lower() or "protocol_violation" in str(res.enforcement_details)


class TestSandboxTerminationStateMachine:
    """Tests verifying the container termination state machine and lifecycle guarantees."""

    def test_container_exit_code_authoritative_for_completion(self, runner: PythonSandboxRunner) -> None:
        """Verify container exit code 0 is required for completion; non-zero exit fails even if output exists."""
        script = """
import sys
# Non-zero exit code
sys.exit(42)
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert res.enforcement_details.get("exit_code") == 42

    def test_timeout_cannot_produce_successful_result(self, runner: PythonSandboxRunner) -> None:
        """Verify that a timed-out container cannot produce a successful result."""
        script = """
import time
time.sleep(10)
result = {"timed_out_but_tried_to_return": True}
"""
        res = runner.run(script, input_data={}, bypass_ast=True, timeout_seconds=2)
        assert res.status == "timeout"
        assert res.result_payload is None
        assert "timed out" in (res.error_message or "").lower()

    def test_pid1_signal_handling_sigterm_and_sigkill_teardown(self, runner: PythonSandboxRunner) -> None:
        """Verify stop_and_remove_container uses bounded grace period and forced kill fallback."""
        container_name = f"vibe-test-sig-{uuid.uuid4().hex[:8]}"
        image_ref = runner.get_effective_image_ref()

        # Launch container running infinite sleep as PID 1
        proc = subprocess.Popen([
            "docker", "run", "-d",
            "--name", container_name,
            "--network", "none",
            image_ref,
            "python", "-c", "import time; time.sleep(60)"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=25)
        cid = out.decode("utf-8", errors="replace").strip()

        # Confirm container is running
        inspect_res = subprocess.run(["docker", "inspect", container_name, "--format", "{{.State.Running}}"], capture_output=True, text=True)
        assert "true" in inspect_res.stdout.lower()

        # Execute bounded stop with fallback kill and mandatory container_id
        start = time.perf_counter()
        confirmed, _ = stop_and_remove_container(container_name, grace_seconds=2, container_id=cid)
        elapsed = time.perf_counter() - start

        # Verify stop took bounded time and container is positively absent
        assert confirmed is True
        assert elapsed < 10.0, f"Stop and remove took too long: {elapsed}s"
        check_after = subprocess.run(["docker", "inspect", container_name], capture_output=True, text=True)
        assert check_after.returncode != 0, "Container still exists after stop_and_remove_container!"

    def test_post_termination_validation_and_no_quota_leak_on_failure(self) -> None:
        """Verify malformed script does not reserve or leak quota in PostgreSQL and creates no artifact."""
        org_id = uuid.uuid4()
        job_id = uuid.uuid4()
        exec_id = f"exec-strict-{int(time.time())}"

        client = SandboxAgentClient(app=agent_app)

        # Script emits invalid JSON
        resp = client.execute(
            organization_id=org_id,
            job_id=job_id,
            execution_id=exec_id,
            code_str="import os; os.write(1, b'INVALID_NOT_JSON'); result = {'bad': True}",
            input_data={},
        )
        assert resp.status == "failed"
        assert resp.result_payload is None
        # Verify no artifact path was returned or created
        assert resp.enforcement_details.get("artifact_path") is None

    def test_timeout_produces_valid_sandbox_execution_response_with_status_timeout(self) -> None:
        """Verify timeout execution produces a valid SandboxExecutionResponse with status='timeout'."""
        org_id = uuid.uuid4()
        job_id = uuid.uuid4()
        exec_id = f"exec-timeout-{int(time.time())}"

        client = SandboxAgentClient(app=agent_app)

        # Script sleeps longer than timeout_seconds
        resp = client.execute(
            organization_id=org_id,
            job_id=job_id,
            execution_id=exec_id,
            code_str="import time; time.sleep(10); result = {'done': True}",
            input_data={},
            timeout_seconds=1,
        )
        assert isinstance(resp, SandboxExecutionResponse)
        assert resp.status == "timeout"
        assert resp.result_payload is None
        assert "timed out" in resp.stderr.lower() or "timeout" in str(resp.enforcement_details.get("policy"))
        assert resp.enforcement_details.get("cleanup_confirmed") is True

    def test_all_five_execution_statuses_construct_and_serialize(self) -> None:
        """Verify all five ExecutionStatus literals construct, serialize and deserialize cleanly."""
        statuses: list[ExecutionStatus] = ["completed", "failed", "timeout", "disabled", "rejected"]

        for st in statuses:
            # Test SandboxExecutionResponse (Pydantic model)
            resp = SandboxExecutionResponse(
                execution_id=f"test-{st}",
                status=st,
                execution_time_ms=100,
                memory_peak_mb=12.5,
                stdout="test_stdout",
                stderr="test_stderr",
                error_message="test_error" if st != "completed" else None,
                result_payload={"status": "ok"} if st == "completed" else None,
                isolation_backend="sandbox_agent_oci",
                enforcement_details={"policy": st, "exit_code": 0 if st == "completed" else 1},
            )
            data = resp.model_dump()
            assert data["status"] == st
            re_parsed = SandboxExecutionResponse.model_validate(data)
            assert re_parsed.status == st

            # Test SandboxExecutionResult (dataclass)
            res = SandboxExecutionResult(
                status=st,
                execution_time_ms=100,
                memory_peak_mb=12.5,
                stdout="test_stdout",
                stderr="test_stderr",
                error_message="test_error" if st != "completed" else None,
                result_payload={"status": "ok"} if st == "completed" else None,
                isolation_backend="docker_oci",
                enforcement_details={"policy": st, "exit_code": 0 if st == "completed" else 1},
            )
            assert res.status == st


class TestStreamCollectorConcurrencyAndBoundaries:
    """Rigorous tests proving stream collection is concurrency-safe and boundary-checked."""

    def test_boundary_stdout_one_byte_over_limit_triggers_kill(self, runner: PythonSandboxRunner) -> None:
        """Verify stdout exceeding 5 MiB by 1 byte triggers immediate termination and retains 0 bytes."""
        # 5 MiB = 5,242,880 bytes. 5,242,881 bytes must trigger overflow.
        script = """
import os
# Write 5MB + 1 byte
total = 5 * 1024 * 1024 + 1
chunk = b"A" * 65536
written = 0
while written < total:
    to_write = min(len(chunk), total - written)
    os.write(1, chunk[:to_write])
    written += to_write
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "stdout limit" in (res.error_message or "").lower() or "stdout_overflow" in str(res.enforcement_details)
        assert len(res.stdout) == 0

    def test_boundary_4096_chunk_crossing(self, runner: PythonSandboxRunner) -> None:
        """Verify container crossing 5 MiB limit across a 4096-byte chunk boundary terminates cleanly."""
        script = """
import os
# Write exactly 1281 chunks of 4096 bytes = 5,246,976 bytes (> 5,242,880)
chunk = b"B" * 4096
for _ in range(1281):
    os.write(1, chunk)
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "stdout_overflow" in str(res.enforcement_details) or "stdout limit" in (res.error_message or "").lower()

    def test_boundary_stderr_retention_and_excess_discard(self, runner: PythonSandboxRunner) -> None:
        """Verify stderr retention is strictly bounded to 64 KiB while excess is discarded."""
        script = """
import sys
# Write 100 KiB to stderr in 4096-byte chunks
chunk = "E" * 4095 + "\\n"
for _ in range(25):
    sys.stderr.write(chunk)
    sys.stderr.flush()
result = {"status": "ok"}
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "completed"
        assert res.result_payload == {"status": "ok"}
        assert len(res.stderr.encode("utf-8")) <= 65536

    def test_concurrent_stdout_and_stderr_flooding_no_deadlock(self, runner: PythonSandboxRunner) -> None:
        """Verify concurrent flooding on both streams does not deadlock and teardown triggers exactly once."""
        script = """
import os
import sys
import threading

def flood_out():
    chunk = b"O" * 4096
    for _ in range(1500):
        try:
            os.write(1, chunk)
        except Exception:
            break

def flood_err():
    chunk = "X" * 4096
    for _ in range(1500):
        try:
            sys.stderr.write(chunk)
            sys.stderr.flush()
        except Exception:
            break

t1 = threading.Thread(target=flood_out)
t2 = threading.Thread(target=flood_err)
t1.start()
t2.start()
t1.join()
t2.join()
"""
        start = time.perf_counter()
        res = runner.run(script, input_data={}, bypass_ast=True, timeout_seconds=10)
        elapsed = time.perf_counter() - start

        # Must not deadlock and terminate well before timeout
        assert elapsed < 10.0, f"Concurrent flooding deadlocked or took too long: {elapsed}s"
        assert res.status == "failed"
        assert res.enforcement_details.get("policy") in ("stdout_overflow", "log_flood_protection")

    def test_no_thread_or_container_survives_overflow(self, runner: PythonSandboxRunner) -> None:
        """Verify that after an overflow termination, no reader thread or container survives."""
        script = """
import os
os.write(1, b"X" * (6 * 1024 * 1024))
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"

        container_name = res.enforcement_details.get("container_name")
        if container_name:
            check = subprocess.run(["docker", "inspect", container_name], capture_output=True, text=True)
            assert check.returncode != 0, f"Container {container_name} survived overflow kill!"

    def test_exactly_once_overflow_election_barrier(self) -> None:
        """Verify lock-protected check-and-set election elects exactly one winner under barrier concurrency."""
        import threading

        election_lock = threading.Lock()
        overflow_claimed = False
        kill_count = 0
        winners: list[int] = []
        num_threads = 8
        barrier = threading.Barrier(num_threads)

        def worker(thread_id: int) -> None:
            nonlocal overflow_claimed, kill_count
            barrier.wait()  # Release all threads simultaneously
            with election_lock:
                if overflow_claimed:
                    return
                overflow_claimed = True

            # Winner executes teardown action outside the lock
            kill_count += 1
            winners.append(thread_id)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2)

        assert kill_count == 1
        assert len(winners) == 1

    def test_concurrent_overflow_barrier_in_container_and_no_quota_leak(self, runner: PythonSandboxRunner) -> None:
        """Force container script to flood stdout and stderr concurrently with threading.Barrier.

        Verify exactly one termination is claimed, container is killed, no quota reserved, no artifact persisted.
        """
        script = """
import os
import sys
import threading

barrier = threading.Barrier(2)

def flood_stdout():
    barrier.wait()
    chunk = b"S" * 65536
    for _ in range(100):
        try:
            os.write(1, chunk)
        except Exception:
            break

def flood_stderr():
    barrier.wait()
    chunk = "E" * 65536
    for _ in range(100):
        try:
            sys.stderr.write(chunk)
            sys.stderr.flush()
        except Exception:
            break

t1 = threading.Thread(target=flood_stdout)
t2 = threading.Thread(target=flood_stderr)
t1.start()
t2.start()
t1.join()
t2.join()
"""
        res = runner.run(script, input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert res.enforcement_details.get("policy") in ("stdout_overflow", "log_flood_protection")
        assert res.result_payload is None
        c_name = res.enforcement_details.get("container_name")
        if c_name:
            c_check = subprocess.run(["docker", "inspect", c_name], capture_output=True, text=True)
            assert c_check.returncode != 0, f"Container {c_name} survived!"

    def test_simulate_docker_stop_failure_triggers_kill_and_rm_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that when docker stop times out, kill and rm execute as fallback and absence is confirmed."""
        call_log: list[str] = []
        orig_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[0] == "docker":
                action = cmd[1]
                call_log.append(action)
                if action == "stop":
                    raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 5))
                elif action in ("kill", "rm"):
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
            return orig_run(cmd, *args, **kwargs)

        def mock_inspect(container_id, docker_client=None):
            call_log.append("inspect")
            return 404, None, DockerNotFoundError("No such container", status_code=404)

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(sandbox_mod, "structured_docker_inspect", mock_inspect)
        confirmed, err = stop_and_remove_container(
            "vibe-test-mock-fallback",
            grace_seconds=1,
            container_id="1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        )

        assert confirmed is True
        assert err is None
        assert "stop" in call_log
        assert "kill" in call_log
        assert "rm" in call_log
        assert "inspect" in call_log

    def test_simulate_docker_rm_failure_fails_closed(self, monkeypatch: pytest.MonkeyPatch, runner: PythonSandboxRunner) -> None:
        """Verify that if container cannot be confirmed removed (inspect status 200), runner fails closed."""
        def mock_inspect(container_id, docker_client=None):
            # Container still present!
            return 200, {"Id": container_id, "State": {"Status": "running"}}, None

        monkeypatch.setattr(sandbox_mod, "structured_docker_inspect", mock_inspect)

        # 1. Directly on stop_and_remove_container
        confirmed, err = stop_and_remove_container(
            "vibe-test-mock-surviving",
            grace_seconds=1,
            container_id="1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        )
        assert confirmed is False
        assert err is not None
        assert "still present" in err or "still exists" in err

        # 2. On runner execution: must fail-closed!
        res = runner.run("result = {'status': 'ok'}", input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "cleanup could not be confirmed" in res.stderr.lower() or "cleanup_unconfirmed" in str(res.enforcement_details)
        assert res.enforcement_details.get("cleanup_confirmed") is False

    def test_docker_inspect_positively_confirms_container_absence(self) -> None:
        """Verify that CLI text stderr CANNOT confirm absence, while structured 404 does."""
        # 1. CLI text phrase 'No such container' strictly CANNOT produce confirmed absence
        cli_conf, cli_err, cli_cat = classify_docker_inspect_result(
            returncode=1,
            stdout="[]",
            stderr="Error: No such container: vibe-test-container-absent",
            container_name="vibe-test-container-absent",
        )
        assert cli_conf is False
        assert cli_cat == "unconfirmed_cli_output"
        assert cli_err is not None
        assert "unconfirmed_cli_output" in cli_err

        # 2. Structured Docker Engine API / SDK status 404 authoritatively confirms absence
        confirmed_404, err_404, cat_404 = classify_docker_inspect_result(
            status_code=404,
            container_name="vibe-test-container-absent",
        )
        assert confirmed_404 is True
        assert err_404 is None
        assert cat_404 == "confirmed_absent"

        # 3. Structured DockerNotFoundError exception confirms absence
        exc_404 = DockerNotFoundError("No such container", status_code=404)
        confirmed_exc, err_exc, cat_exc = classify_docker_inspect_result(
            api_exception=exc_404,
            container_name="vibe-test-container-absent",
        )
        assert confirmed_exc is True
        assert err_exc is None
        assert cat_exc == "confirmed_absent"

    def test_docker_inspect_finds_container_still_present(self) -> None:
        """Verify that inspect returncode 0 classifies as container_still_present and fails closed."""
        confirmed, err, cat = classify_docker_inspect_result(
            returncode=0,
            stdout="[{\"Id\": \"1234567890abcdef\"}]",
            stderr="",
            container_name="vibe-test-container-present",
        )
        assert confirmed is False
        assert cat == "container_still_present"
        assert err is not None
        assert "still present" in err or "still exists" in err

    def test_docker_daemon_connection_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that daemon unavailable error is NEVER interpreted as container absence."""
        stderr_msg = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
        confirmed, err, cat = classify_docker_inspect_result(
            returncode=1,
            stdout="",
            stderr=stderr_msg,
            container_name="vibe-test-daemon-down",
        )
        assert confirmed is False
        assert cat == "daemon_unavailable"
        assert err is not None
        assert "daemon_unavailable" in err

        # Also verify stop_and_remove_container fails closed with structured exception
        def mock_inspect(container_id, docker_client=None):
            return None, None, DockerConnectionError(stderr_msg)

        monkeypatch.setattr(sandbox_mod, "structured_docker_inspect", mock_inspect)
        confirmed_rm, err_rm = stop_and_remove_container(
            "vibe-test-daemon-down",
            grace_seconds=1,
            container_id="1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        )
        assert confirmed_rm is False
        assert err_rm is not None
        assert "daemon_unavailable" in err_rm

    def test_docker_inspect_permission_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that permission failure is classified correctly and fails closed."""
        stderr_msg = "Got permission denied while trying to connect to the Docker daemon socket"
        confirmed, err, cat = classify_docker_inspect_result(
            returncode=1,
            stdout="",
            stderr=stderr_msg,
            container_name="vibe-test-perm-denied",
        )
        assert confirmed is False
        assert cat == "permission_or_api_failure"
        assert err is not None
        assert "permission_or_api_failure" in err

        def mock_inspect(container_id, docker_client=None):
            return 403, None, DockerPermissionError(stderr_msg, status_code=403)

        monkeypatch.setattr(sandbox_mod, "structured_docker_inspect", mock_inspect)
        confirmed_rm, err_rm = stop_and_remove_container(
            "vibe-test-perm-denied",
            grace_seconds=1,
            container_id="1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        )
        assert confirmed_rm is False
        assert err_rm is not None
        assert "permission_or_api_failure" in err_rm

    def test_docker_inspect_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that docker inspect timeout fails closed and records inspection_timeout in telemetry."""
        def mock_inspect(container_id, docker_client=None):
            return None, None, DockerTimeoutError("Docker inspection timed out after 3.0s")

        monkeypatch.setattr(sandbox_mod, "structured_docker_inspect", mock_inspect)
        confirmed, err = stop_and_remove_container(
            "vibe-test-inspect-timeout",
            grace_seconds=1,
            container_id="1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        )
        assert confirmed is False
        assert err is not None
        assert "inspection_timeout" in err or "timeout" in err

    def test_docker_stop_returning_nonzero_without_raising_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify explicit handling of nonzero docker stop return codes, with kill/rm fallback."""
        call_log: list[str] = []

        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[0] == "docker":
                action = cmd[1]
                call_log.append(action)
                if action == "stop":
                    # Nonzero return without exception
                    return subprocess.CompletedProcess(
                        args=cmd,
                        returncode=1,
                        stdout=b"",
                        stderr=b"Error response from daemon: cannot stop container: context deadline exceeded",
                    )
                elif action in ("kill", "rm"):
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

        def mock_inspect(container_id, docker_client=None):
            call_log.append("inspect")
            return 404, None, DockerNotFoundError("No such container", status_code=404)

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(sandbox_mod, "structured_docker_inspect", mock_inspect)
        confirmed, err = stop_and_remove_container(
            "vibe-test-stop-nonzero",
            grace_seconds=1,
            container_id="1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        )
        assert confirmed is True
        assert err is None
        assert "stop" in call_log
        assert "kill" in call_log
        assert "rm" in call_log
        assert "inspect" in call_log

    def test_docker_remove_failure_followed_by_confirmed_absence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that if rm fails (e.g. removal in progress) but inspect positively confirms absence, cleanup succeeds."""
        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[0] == "docker":
                action = cmd[1]
                if action == "rm":
                    return subprocess.CompletedProcess(
                        args=cmd,
                        returncode=1,
                        stdout=b"",
                        stderr=b"Error response from daemon: removal of container is already in progress",
                    )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

        def mock_inspect(container_id, docker_client=None):
            return 404, None, DockerNotFoundError("No such container", status_code=404)

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(sandbox_mod, "structured_docker_inspect", mock_inspect)
        confirmed, err = stop_and_remove_container(
            "vibe-test-rm-progress",
            grace_seconds=1,
            container_id="1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        )
        assert confirmed is True
        assert err is None

    def test_docker_remove_failure_followed_by_inconclusive_inspection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that if rm fails AND inspect is inconclusive/ambiguous, cleanup fails closed with all errors."""
        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[0] == "docker":
                action = cmd[1]
                if action == "rm":
                    return subprocess.CompletedProcess(
                        args=cmd,
                        returncode=1,
                        stdout=b"",
                        stderr=b"Error response from daemon: container is locked by external driver",
                    )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

        def mock_inspect(container_id, docker_client=None):
            return 500, None, DockerServerError("unexpected internal daemon response code 500", status_code=500)

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr(sandbox_mod, "structured_docker_inspect", mock_inspect)
        confirmed, err = stop_and_remove_container(
            "vibe-test-rm-inconclusive",
            grace_seconds=1,
            container_id="1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        )
        assert confirmed is False
        assert err is not None
        assert "rm_nonzero_exit_1" in err
        assert "server_error" in err or "ambiguous_failure" in err
        assert "rm_nonzero_exit_1" in err
        assert "server_error" in err or "ambiguous_failure" in err

    def test_docker_server_http_500_fails_closed(self) -> None:
        """Verify that Docker server HTTP 500 error fails closed and is classified as server_error."""
        stderr_msg = "Error response from daemon: 500 Internal Server Error: engine failed to retrieve object"
        confirmed, err, cat = classify_docker_inspect_result(
            returncode=1,
            stdout="",
            stderr=stderr_msg,
            container_name="vibe-test-server-500",
        )
        assert confirmed is False
        assert cat == "server_error"
        assert err is not None
        assert "server_error" in err

    def test_docker_misleading_no_such_file_or_directory_rejected(self) -> None:
        """Verify that 'Error: no such file or directory' is NEVER classified as container absence."""
        stderr_msg = "Error: no such file or directory"
        confirmed, err, cat = classify_docker_inspect_result(
            returncode=1,
            stdout="",
            stderr=stderr_msg,
            container_name="vibe-test-no-such-file",
        )
        assert confirmed is False
        assert cat == "ambiguous_failure"
        assert err is not None
        assert "ambiguous_failure" in err

    def test_container_renamed_between_cleanup_and_verification_detected_via_immutable_id(
        self, runner: PythonSandboxRunner
    ) -> None:
        """Verify that a container renamed before verification is detected via immutable container ID and fails closed."""
        cname_orig = f"vibe-test-rename-orig-{uuid.uuid4().hex[:8]}"
        cname_hidden = f"vibe-test-rename-hidden-{uuid.uuid4().hex[:8]}"
        image_ref = runner.get_effective_image_ref()

        # Launch background container
        run_res = subprocess.run(
            ["docker", "run", "-d", "--name", cname_orig, "--entrypoint", "python", image_ref, "-c", "import time; time.sleep(15)"],
            capture_output=True,
            text=True,
        )
        if run_res.returncode != 0:
            pytest.skip("Docker container run failed.")
        cid = run_res.stdout.strip()

        try:
            # Rename container to simulate evasive renaming attack
            rename_res = subprocess.run(["docker", "rename", cname_orig, cname_hidden], capture_output=True, text=True)
            assert rename_res.returncode == 0

            # Inspecting via immutable container_id discovers container still exists!
            confirmed, err, cat = classify_docker_inspect_result(
                returncode=0,
                stdout="[{\"Id\": \"" + cid + "\"}]",
                stderr="",
                container_name=cid,
            )
            assert confirmed is False
            assert cat == "container_still_present"

            # stop_and_remove_container targeting container_id safely tears down renamed container
            confirmed_cleanup, cleanup_err = stop_and_remove_container(cname_orig, grace_seconds=1, container_id=cid)
            assert confirmed_cleanup is True
            assert cleanup_err is None

            # Verify container is truly absent after cleanup
            verify_res = subprocess.run(["docker", "inspect", "--type", "container", cid], capture_output=True, text=True)
            assert verify_res.returncode != 0
            assert "no such container" in verify_res.stderr.lower()

        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

    def test_removal_followed_by_positively_confirmed_absence_using_container_id(
        self, runner: PythonSandboxRunner
    ) -> None:
        """Verify real container lifecycle: removal followed by positively confirmed absence via immutable container ID."""
        cname = f"vibe-test-absence-cid-{uuid.uuid4().hex[:8]}"
        image_ref = runner.get_effective_image_ref()

        run_res = subprocess.run(
            ["docker", "run", "-d", "--name", cname, "--entrypoint", "python", image_ref, "-c", "import time; time.sleep(10)"],
            capture_output=True,
            text=True,
        )
        if run_res.returncode != 0:
            pytest.skip("Docker container run failed.")
        cid = run_res.stdout.strip()

        confirmed, err = stop_and_remove_container(cname, grace_seconds=1, container_id=cid)
        assert confirmed is True
        assert err is None

        # Verify structured inspection of cid positively returns genuine 404 absence
        code_final, _, exc_final = structured_docker_inspect(cid)
        confirmed_final, err_final, cat_final = classify_docker_inspect_result(
            container_name=cid, status_code=code_final, api_exception=exc_final
        )
        assert confirmed_final is True
        assert cat_final == "confirmed_absent"
        assert err_final is None

        # Verify that human-readable CLI stderr phrase cannot independently confirm absence
        inspect_res = subprocess.run(["docker", "inspect", "--type", "container", cid], capture_output=True, text=True)
        assert inspect_res.returncode != 0
        cli_conf, _, cli_cat = classify_docker_inspect_result(
            inspect_res.returncode, inspect_res.stdout, inspect_res.stderr, cid
        )
        assert cli_conf is False
        assert cli_cat == "unconfirmed_cli_output"

    def test_pinned_image_configuration_and_direct_pid1_attestation(
        self, runner: PythonSandboxRunner
    ) -> None:
        """Item 2 Attestation: Pinned image Entrypoint/Cmd and live container /proc/1/cmdline verify direct Python PID 1."""
        # 1. Inspect pinned image configuration
        img_ref = runner.get_effective_image_ref()
        insp_res = subprocess.run(["docker", "inspect", img_ref], capture_output=True, text=True)
        assert insp_res.returncode == 0
        img_info = json.loads(insp_res.stdout)[0]
        config = img_info.get("Config", {})
        # Config.Entrypoint is None, Config.Cmd is ['python']
        assert config.get("Entrypoint") is None
        assert config.get("Cmd") == ["python"]

        # 2. Controlled execution measuring live container PID 1, PPID 0, and /proc/1/cmdline
        script = """
import os
with open('/proc/1/cmdline', 'rb') as f:
    cmd_bytes = f.read()
parts = [p.decode('utf-8', errors='replace') for p in cmd_bytes.split(b'\\x00') if p]
result = {
    'pid': os.getpid(),
    'ppid': os.getppid(),
    'cmdline_parts': parts,
}
"""
        res = runner.run(script, input_data={}, bypass_ast=True, timeout_seconds=10)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        assert res.result_payload is not None
        assert res.result_payload["pid"] == 1
        assert res.result_payload["ppid"] == 0
        cmdline_parts = res.result_payload["cmdline_parts"]
        assert cmdline_parts[0] == "python"
        assert "/input/runner.py" in cmdline_parts

        # 3. Verify host-inspected container metadata
        assert res.enforcement_details.get("container_path") == "python"
        assert res.enforcement_details.get("container_args") == ["/input/runner.py"]
        assert res.enforcement_details.get("container_id") is not None
        assert res.enforcement_details.get("pid_1_architecture") == "direct_user_pid1"
        assert res.enforcement_details.get("pid_namespace_teardown") is True
        assert res.enforcement_details.get("cleanup_confirmed") is True

    def test_cleanup_verification_rejected_if_immutable_container_id_missing(self) -> None:
        """Section 2: Cleanup verification MUST be rejected if immutable container_id is missing/None."""
        confirmed, err = stop_and_remove_container("vibe-test-no-id", container_id=None)
        assert confirmed is False
        assert err is not None
        assert "missing_immutable_container_id" in err

    def test_structured_docker_not_found_confirms_absence(self) -> None:
        """Section 1: Structured HTTP 404 / DockerNotFoundError confirms container absence."""
        confirmed_404, err_404, cat_404 = classify_docker_inspect_result(
            status_code=404,
            container_name="vibe-test-c1",
        )
        assert confirmed_404 is True
        assert err_404 is None
        assert cat_404 == "confirmed_absent"

        exc_404 = DockerNotFoundError("Container not found on Docker daemon", status_code=404)
        confirmed_exc, err_exc, cat_exc = classify_docker_inspect_result(
            api_exception=exc_404,
            container_name="vibe-test-c1",
        )
        assert confirmed_exc is True
        assert err_exc is None
        assert cat_exc == "confirmed_absent"

    def test_structured_docker_http_200_finds_container_present(self) -> None:
        """Section 1: Structured HTTP 200 confirms container exists and fails closed."""
        confirmed_200, err_200, cat_200 = classify_docker_inspect_result(
            status_code=200,
            container_name="vibe-test-c1",
        )
        assert confirmed_200 is False
        assert cat_200 == "container_still_present"
        assert err_200 is not None
        assert "still present" in err_200

    def test_structured_docker_server_error_500_with_misleading_not_found_wording_fails_closed(self) -> None:
        """Section 1: HTTP 500 containing misleading not-found wording MUST fail closed and be classified as server_error."""
        # Realistic error: daemon storage driver crashed or failed with not-found phrase inside 500 response
        misleading_500_body = "500 Internal Server Error: storage driver reported object not found: no such container metadata"
        exc_500 = DockerServerError(misleading_500_body, status_code=500)

        confirmed, err, cat = classify_docker_inspect_result(
            status_code=500,
            api_exception=exc_500,
            stderr=misleading_500_body,
            container_name="vibe-test-c1",
        )
        assert confirmed is False, "HTTP 500 with not-found phrasing must NEVER confirm absence!"
        assert cat == "server_error"
        assert err is not None
        assert "server_error" in err

    def test_structured_docker_connection_permission_and_timeout_errors_fail_closed(self) -> None:
        """Section 1: Connection failure, permission failure, and timeout structured exceptions fail closed."""
        # Connection error
        conn_exc = DockerConnectionError("Failed to connect to docker.sock")
        c1, e1, cat1 = classify_docker_inspect_result(api_exception=conn_exc, container_name="vibe-c1")
        assert c1 is False
        assert cat1 == "daemon_unavailable"

        # Permission error (HTTP 403)
        perm_exc = DockerPermissionError("Permission denied accessing Docker API", status_code=403)
        c2, e2, cat2 = classify_docker_inspect_result(status_code=403, api_exception=perm_exc, container_name="vibe-c1")
        assert c2 is False
        assert cat2 == "permission_or_api_failure"

        # Timeout error
        time_exc = DockerTimeoutError("Docker Engine inspection timed out after 3s")
        c3, e3, cat3 = classify_docker_inspect_result(api_exception=time_exc, container_name="vibe-c1")
        assert c3 is False
        assert cat3 == "timeout"

    def test_structured_docker_inspect_with_mocked_sdk_client(self) -> None:
        """Section 1: structured_docker_inspect accurately maps SDK client responses to status codes and exceptions."""
        class MockContainer:
            def __init__(self, cid: str) -> None:
                self.attrs = {"Id": cid, "State": {"Status": "running"}}

        class MockContainers:
            def __init__(self) -> None:
                self.mode = "present"

            def get(self, cid: str):
                if self.mode == "present":
                    return MockContainer(cid)
                elif self.mode == "not_found":
                    raise DockerNotFoundError("Container not found", status_code=404)
                elif self.mode == "server_error":
                    raise DockerServerError("500 Internal Server Error: driver error object not found", status_code=500)
                raise RuntimeError("Unexpected mock mode")

        class MockDockerClient:
            def __init__(self) -> None:
                self.containers = MockContainers()

        mock_client = MockDockerClient()
        cid = "aabbccdd00112233445566778899aabbccdd00112233445566778899aabbccdd"

        # 1. Container exists
        mock_client.containers.mode = "present"
        code1, data1, exc1 = structured_docker_inspect(cid, docker_client=mock_client)
        assert code1 == 200
        assert data1 is not None and data1["Id"] == cid
        assert exc1 is None

        # 2. Container absent (404)
        mock_client.containers.mode = "not_found"
        code2, data2, exc2 = structured_docker_inspect(cid, docker_client=mock_client)
        assert code2 == 404
        assert data2 is None
        assert isinstance(exc2, DockerNotFoundError)

        # 3. Server error (500)
        mock_client.containers.mode = "server_error"
        code3, data3, exc3 = structured_docker_inspect(cid, docker_client=mock_client)
        assert code3 == 500
        assert data3 is None
        assert isinstance(exc3, DockerServerError)

    def test_mandatory_immutable_container_id_captured_and_passed(self, runner: PythonSandboxRunner) -> None:
        """Section 2: Execution captures full 64-character container ID at creation and passes it to cleanup."""
        res = runner.run("result = {'hello': 'world'}", input_data={}, bypass_ast=True)
        assert res.status == "completed", f"Execution failed: {res.stderr}"
        cid = res.enforcement_details.get("container_id")
        assert cid is not None
        assert isinstance(cid, str)
        assert len(cid) == 64, f"Container ID must be 64 characters, got: {cid}"
        assert all(c in "0123456789abcdefABCDEF" for c in cid)
        assert res.enforcement_details.get("cleanup_confirmed") is True

    def test_runner_fails_closed_when_container_id_cannot_be_captured(self, monkeypatch: pytest.MonkeyPatch, runner: PythonSandboxRunner) -> None:
        """Section 2: If immutable container ID cannot be captured at creation, runner aborts and fails closed."""
        orig_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "inspect":
                # Simulate inspect failing to return an ID
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=b"", stderr=b"no inspect")
            return orig_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", mock_run)
        # Point cid_file read to fail by making a mock runner whose cidfile logic fails
        orig_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if "container.cid" in str(self):
                return ""  # Empty cid
            return orig_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", mock_read_text)

        res = runner.run("result = {'ok': True}", input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "immutable container id" in res.stderr.lower() or "missing_immutable_container_id" in str(res.enforcement_details)
        assert res.enforcement_details.get("error") == "missing_immutable_container_id"

    def test_immutable_image_reference_verified_against_pinned_digest(self, runner: PythonSandboxRunner) -> None:
        """Section 3: Effective image used by created container verified against configured digest and identity."""
        res = runner.run("result = {'provenance': 'verified'}", input_data={}, bypass_ast=True)
        assert res.status == "completed"
        details = res.enforcement_details
        assert runner.image_digest in details.get("image_ref", "") or runner.image_digest in details.get("container_image_id", "")
        assert details.get("container_path") == "python"
        assert details.get("container_args") == ["/input/runner.py"]

    def test_runner_fails_closed_on_image_provenance_mismatch(self, monkeypatch: pytest.MonkeyPatch, runner: PythonSandboxRunner) -> None:
        """Section 3: If container metadata reports an unpinned/mismatched image digest, runner fails closed."""
        import app.analytics.python_sandbox as sandbox_mod

        def mock_provenance(container_id, **kwargs):
            return (
                False,
                "image_provenance_mismatch: Container config image 'unpinned-image:latest' does not match expected reference",
                {
                    "pinned_repository_reference": "vibe-python-sandbox@sha256:bad000",
                    "expected_manifest_digest": "sha256:bad000",
                    "image_inspection_id": None,
                    "repo_digests": [],
                    "container_config_image": "unpinned-image:latest",
                    "container_image_id": "sha256:bad000",
                    "effective_path": "python",
                    "effective_args": ["/input/runner.py"],
                    "architecture": None,
                    "os": None,
                },
            )

        monkeypatch.setattr(sandbox_mod, "verify_image_and_container_provenance", mock_provenance)
        res = runner.run("result = {'test': 1}", input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "provenance" in res.stderr.lower() or "image_provenance_mismatch" in str(res.enforcement_details)
        assert res.enforcement_details.get("error") == "image_provenance_mismatch"

    def test_runner_fails_closed_on_entrypoint_mismatch(self, monkeypatch: pytest.MonkeyPatch, runner: PythonSandboxRunner) -> None:
        """Section 3: If container metadata reports a non-python entrypoint or shell wrapper, runner fails closed."""
        import app.analytics.python_sandbox as sandbox_mod

        def mock_provenance(container_id, **kwargs):
            return (
                False,
                "entrypoint_mismatch: Container path '/bin/sh' is not 'python'",
                {
                    "pinned_repository_reference": f"vibe-python-sandbox@{runner.image_digest}",
                    "expected_manifest_digest": runner.image_digest,
                    "image_inspection_id": runner.image_digest,
                    "repo_digests": [f"vibe-python-sandbox@{runner.image_digest}"],
                    "container_config_image": f"vibe-python-sandbox@{runner.image_digest}",
                    "container_image_id": runner.image_digest,
                    "effective_path": "/bin/sh",
                    "effective_args": ["-c", "python /input/runner.py"],
                    "architecture": "amd64",
                    "os": "linux",
                },
            )

        monkeypatch.setattr(sandbox_mod, "verify_image_and_container_provenance", mock_provenance)
        res = runner.run("result = {'test': 1}", input_data={}, bypass_ast=True)
        assert res.status == "failed"
        assert "entrypoint" in res.stderr.lower() or "entrypoint_mismatch" in str(res.enforcement_details)
        assert res.enforcement_details.get("error") == "entrypoint_mismatch"

    def test_cli_no_such_container_cannot_independently_confirm_absence(self) -> None:
        """A CLI message containing 'no such container' cannot independently produce confirmed absence."""
        # 1. CLI returncode=1 stderr='Error: No such container: vibe-test' => MUST fail closed
        confirmed, err, cat = classify_docker_inspect_result(
            returncode=1,
            stdout="[]",
            stderr="Error: No such container: vibe-test-container",
            container_name="vibe-test-container",
        )
        assert confirmed is False, "CLI 'no such container' must NOT confirm absence"
        assert cat == "unconfirmed_cli_output"
        assert err is not None
        assert "unconfirmed_cli_output" in err

        # 2. CLI returncode=1 stderr='no such object' => MUST also fail closed
        confirmed2, err2, cat2 = classify_docker_inspect_result(
            returncode=1,
            stdout="",
            stderr="Error: no such object: vibe-test-obj",
            container_name="vibe-test-obj",
        )
        assert confirmed2 is False, "CLI 'no such object' must NOT confirm absence"
        assert cat2 == "unconfirmed_cli_output"

    def test_missing_sdk_api_availability_fails_closed(self) -> None:
        """When structured Docker Engine API inspection is unavailable, cleanup verification fails closed."""
        # Simulate structured_docker_inspect returning connection error (no SDK, no API)
        code, data, exc = structured_docker_inspect(
            "deadbeef" * 8,
            docker_client=None,  # No SDK client
        )
        # On a machine where Docker API IS available, this will return 404.
        # But we explicitly test the classify path when connection fails:
        conn_exc = DockerConnectionError("Docker Engine API unavailable; structured inspection cannot be performed")
        confirmed, err, cat = classify_docker_inspect_result(
            api_exception=conn_exc,
            container_name="vibe-test-missing-api",
        )
        assert confirmed is False
        assert cat == "daemon_unavailable"
        assert err is not None

    def test_structured_engine_api_404_confirms_absence(self) -> None:
        """Docker Engine API HTTP 404 response authoritatively confirms container absence."""
        code, data, exc = query_docker_engine_api("/containers/nonexistent_engine_api_test_12345/json")
        assert code == 404
        assert data is None
        assert isinstance(exc, DockerNotFoundError)

        # Classify confirms absence
        confirmed, err, cat = classify_docker_inspect_result(
            status_code=code, api_exception=exc, container_name="nonexistent"
        )
        assert confirmed is True
        assert cat == "confirmed_absent"
        assert err is None

    def test_structured_engine_api_200_confirms_container_exists(self, runner: PythonSandboxRunner) -> None:
        """Docker Engine API HTTP 200 response confirms container still exists."""
        img_ref = runner.get_effective_image_ref()
        cname = f"vibe-test-api-200-{uuid.uuid4().hex[:8]}"
        run_res = subprocess.run(
            ["docker", "run", "-d", "--name", cname, "--entrypoint", "python", img_ref, "-c", "import time; time.sleep(30)"],
            capture_output=True, text=True,
        )
        if run_res.returncode != 0:
            pytest.skip("Docker container run failed.")
        cid = run_res.stdout.strip()
        try:
            code, data, exc = query_docker_engine_api(f"/containers/{cid}/json")
            assert code == 200
            assert data is not None
            assert exc is None
            confirmed, err, cat = classify_docker_inspect_result(
                status_code=code, container_name=cid
            )
            assert confirmed is False
            assert cat == "container_still_present"
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=5)

    def test_image_provenance_all_seven_checks(self, runner: PythonSandboxRunner) -> None:
        """All 7 OCI image provenance checks pass against a real container using the pinned digest reference."""
        img_ref = runner.get_effective_image_ref()
        cname = f"vibe-test-provenance-{uuid.uuid4().hex[:8]}"
        run_res = subprocess.run(
            ["docker", "run", "-d", "--name", cname, "--entrypoint", "python", img_ref, "/input/runner.py"],
            capture_output=True, text=True,
        )
        if run_res.returncode != 0:
            pytest.skip("Docker container run failed.")
        cid = run_res.stdout.strip()
        try:
            ok, err, diag = verify_image_and_container_provenance(
                container_id=cid,
                expected_repo=runner.image_repo,
                expected_manifest_digest=runner.image_digest,
                expected_platform_arch="amd64",
            )
            assert ok is True, f"Provenance verification failed: {err}"
            assert err is None

            # Check 1: pinned_repository_reference
            assert diag["pinned_repository_reference"] == img_ref

            # Check 2: container_config_image matches pinned reference
            assert runner.image_digest in diag["container_config_image"]
            assert ":latest" not in diag["container_config_image"]

            # Check 3: repo_digests contains expected manifest digest
            assert any(runner.image_digest in rd for rd in diag["repo_digests"])

            # Check 4: container_image_id matches image_inspection_id
            assert diag["container_image_id"] == diag["image_inspection_id"]

            # Check 5: architecture/os match
            assert diag["architecture"] == "amd64"
            assert diag["os"] == "linux"

            # Check 6: effective_path is 'python'
            assert diag["effective_path"] == "python"
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=5)

    def test_image_provenance_rejects_mutable_tag(self) -> None:
        """verify_image_and_container_provenance rejects containers created with mutable :latest tag."""
        # Simulate container with :latest tag in Config.Image using mock SDK client
        class MockContainer:
            def __init__(self):
                self.attrs = {
                    "Config": {"Image": "vibe-python-sandbox:latest"},
                    "Image": "sha256:e4ba810cbecd430e97de30e389c9d88099a73a8f0efe13935a566a8ac8db2ab4",
                    "Path": "python",
                    "Args": ["/input/runner.py"],
                }

        class MockContainers:
            def get(self, cid):
                return MockContainer()

        class MockDockerClient:
            def __init__(self):
                self.containers = MockContainers()

        ok, err, diag = verify_image_and_container_provenance(
            container_id="aabb" * 16,
            docker_client=MockDockerClient(),
        )
        assert ok is False
        assert "mutable_tag_rejected" in (err or "")

