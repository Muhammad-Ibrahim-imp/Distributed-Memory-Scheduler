from common.protocol import dsm_pb2


def test_recovery_fields_survive_wire_round_trip():
    request = dsm_pb2.Envelope()
    request.refresh.request_id = "recovery-1"
    request.refresh.identity = "node-identity"
    request.refresh.credential = "opaque-old-credential"
    request.refresh.enrollment_secret = "opaque-enrollment-factor"
    request.refresh.coordinator_generation = "old-generation"

    decoded = dsm_pb2.Envelope.FromString(request.SerializeToString())
    assert decoded.WhichOneof("payload") == "refresh"
    assert decoded.refresh.request_id == "recovery-1"
    assert decoded.refresh.identity == "node-identity"
    assert decoded.refresh.enrollment_secret == "opaque-enrollment-factor"
    assert decoded.refresh.coordinator_generation == "old-generation"

    response = dsm_pb2.Envelope()
    response.refresh_response.request_id = "recovery-1"
    response.refresh_response.token = "opaque-token"
    response.refresh_response.credential = "opaque-new-credential"
    response.coordinator_generation = "new-generation"
    decoded_response = dsm_pb2.Envelope.FromString(response.SerializeToString())
    assert decoded_response.refresh_response.credential == "opaque-new-credential"
    assert decoded_response.coordinator_generation == "new-generation"
