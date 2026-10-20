"""Vendored Ed25519 verification and signed envelopes (orchestration plan §6.3)."""

from __future__ import annotations

import copy
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mu.orchestration.node import ed25519
from mu.orchestration.node.envelope import (ReplayCache, b64e, dispatch_payload, manifest_digest,
                                            tool_version_payload, verify_cancel, verify_dispatch)

# RFC 8032 §7.1 test vectors: (public key, message, signature).
RFC8032 = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ("ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
     "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
     "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"),
]


@pytest.mark.parametrize("pub,msg,sig", RFC8032)
def test_rfc8032_vectors_verify(pub, msg, sig):
    assert ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig))


@pytest.mark.parametrize("pub,msg,sig", RFC8032)
def test_rfc8032_vectors_reject_any_flipped_bit(pub, msg, sig):
    sig_b = bytearray.fromhex(sig)
    for index in (0, 31, 32, 63):
        tampered = bytearray(sig_b)
        tampered[index] ^= 0x01
        assert not ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes(tampered))
    assert not ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg) + b"x", bytes(sig_b))


def _keypair():
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, pub


def test_interoperates_with_cryptography_signatures():
    for _ in range(5):
        key, pub = _keypair()
        msg = b"mucli" * 37
        assert ed25519.verify(pub, msg, key.sign(msg))
        _other, other_pub = _keypair()
        assert not ed25519.verify(other_pub, msg, key.sign(msg))


def test_rejects_malformed_inputs_and_non_canonical_s():
    key, pub = _keypair()
    sig = key.sign(b"m")
    assert not ed25519.verify(pub[:31], b"m", sig)
    assert not ed25519.verify(pub, b"m", sig[:63])
    assert not ed25519.verify(b"\xff" * 32, b"m", sig)
    # s + q is the classic malleability: must be rejected (s >= q).
    q = 2**252 + 27742317777372353535851937790883648493
    s = int.from_bytes(sig[32:], "little") + q
    if s < 2**256:
        assert not ed25519.verify(pub, b"m", sig[:32] + s.to_bytes(32, "little"))


# --------------------------------------------------------------------- envelopes

def _envelope(key, test_key=None, *, node_id="n_1", test=False, **overrides):
    manifest = {"id": "demo.tool", "version": 1, "effect": "read_only", "category": "diagnostics",
                "implementation": {"runtime": "python3", "entry": "main.py"}}
    blobs = {"main.py": "a" * 64}
    digest_ = manifest_digest(manifest)
    signer = test_key if test else key
    env = {"kind": "dispatch", "exec_id": "x_1", "run_id": "r_1", "node_id": node_id, "tool_id": "demo.tool",
           "version": 1, "test": test, "manifest": manifest, "manifest_digest": digest_, "blobs": blobs,
           "version_sig": b64e(signer.sign(tool_version_payload("demo.tool", 1, digest_, blobs))),
           "params": {"a": 1}, "check_mode": False, "issued_at": time.time(), "deadline": time.time() + 600,
           "nonce": "n" * 32, "secret_names": []}
    env.update(overrides)
    env["sig"] = b64e(key.sign(dispatch_payload(env)))
    return env


def _verify(env, pub, test_pub=None, *, accept_test=False, replay=None, node_id="n_1"):
    return verify_dispatch(env, node_id=node_id, signing_key=pub, test_key=test_pub, accept_test=accept_test,
                           replay=replay or ReplayCache())


def test_valid_envelope_verifies():
    key, pub = _keypair()
    assert _verify(_envelope(key), pub).ok


@pytest.mark.parametrize("field,value", [
    ("params", {"a": 2}), ("check_mode", True), ("tool_id", "other.tool"), ("version", 2),
    ("exec_id", "x_2"), ("deadline", time.time() + 99999), ("secret_names", ["db"]),
])
def test_tampering_any_signed_field_breaks_the_signature(field, value):
    key, pub = _keypair()
    env = _envelope(key)
    env[field] = value
    check = _verify(env, pub)
    assert not check.ok and "signature" in check.reason


def test_secret_values_ride_outside_the_signature_but_names_are_signed():
    key, pub = _keypair()
    env = _envelope(key, secret_names=["db"])
    env["secrets"] = {"db": "hunter2"}
    assert _verify(env, pub).ok


def test_tampered_manifest_is_detected_even_with_valid_dispatch_signature():
    key, pub = _keypair()
    env = _envelope(key)
    env["manifest"] = dict(env["manifest"], effect="destructive")
    env["sig"] = b64e(key.sign(dispatch_payload(env)))  # a master that re-signs a modified manifest
    check = _verify(env, pub)
    assert not check.ok and "digest" in check.reason


def test_tampered_blob_digest_breaks_the_version_signature():
    key, pub = _keypair()
    env = _envelope(key)
    env["blobs"] = {"main.py": "b" * 64}
    env["sig"] = b64e(key.sign(dispatch_payload(env)))
    check = _verify(env, pub)
    assert not check.ok and "tool version" in check.reason


def test_wrong_key_replay_expiry_future_and_misaddressed_are_refused():
    key, pub = _keypair()
    _other, other_pub = _keypair()
    assert "signature" in _verify(_envelope(key), other_pub).reason
    replay = ReplayCache()
    env = _envelope(key)
    assert _verify(env, pub, replay=replay).ok
    assert "replay" in _verify(copy.deepcopy(env), pub, replay=replay).reason
    assert "deadline" in _verify(_envelope(key, deadline=time.time() - 1), pub).reason
    assert "future" in _verify(_envelope(key, issued_at=time.time() + 3600), pub).reason
    assert "another node" in _verify(_envelope(key, node_id="n_2"), pub).reason
    assert "nonce" in _verify(_envelope(key, nonce="short"), pub).reason


def test_test_key_is_accepted_only_by_canaries_and_never_for_published():
    key, pub = _keypair()
    test_key, test_pub = _keypair()
    env = _envelope(key, test_key, test=True)
    assert "canary" in _verify(env, pub, test_pub, accept_test=False).reason
    assert _verify(_envelope(key, test_key, test=True), pub, test_pub, accept_test=True).ok
    # A test-key signature presented as a published version fails the main-key check.
    forged = _envelope(key, test_key, test=True)
    forged["test"] = False
    forged["sig"] = b64e(key.sign(dispatch_payload(forged)))
    assert not _verify(forged, pub, test_pub, accept_test=True).ok


def test_cancel_verification():
    key, pub = _keypair()
    from mu.orchestration.node.envelope import cancel_payload
    cancel = {"kind": "cancel", "exec_id": "x_1", "node_id": "n_1", "nonce": "c" * 32, "issued_at": time.time(),
              "deadline": time.time() + 60}
    cancel["sig"] = b64e(key.sign(cancel_payload(cancel)))
    assert verify_cancel(cancel, node_id="n_1", signing_key=pub).ok
    assert not verify_cancel(dict(cancel, exec_id="x_2"), node_id="n_1", signing_key=pub).ok
    assert not verify_cancel(cancel, node_id="n_2", signing_key=pub).ok


def test_master_keyring_signs_what_nodes_verify(tmp_path):
    from mu.orchestration.signing import KeyRing
    ring = KeyRing(str(tmp_path))
    env = _envelope(Ed25519PrivateKey.generate())
    env.pop("sig")
    blobs = env["blobs"]
    env["version_sig"] = ring.sign_version("demo.tool", 1, env["manifest_digest"], blobs)
    signed = ring.sign_dispatch(env)
    assert _verify(signed, ring.signing_public).ok
    assert oct((tmp_path / "keys" / "signing.key").stat().st_mode & 0o777) == "0o600"
    # Keys persist across instances.
    assert KeyRing(str(tmp_path)).signing_key_id == ring.signing_key_id
