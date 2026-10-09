import os
import unittest

from operational import ed25519_pure as ed
from operational import log_signing as ls


class TestEd25519Pure(unittest.TestCase):
    def test_rfc8032_test_vectors(self):
        vectors = [
            ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
             "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
            ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
             "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ]
        for sk, pk, msg, sig in vectors:
            sk_b, msg_b = bytes.fromhex(sk), bytes.fromhex(msg)
            self.assertEqual(ed.public_key(sk_b).hex(), pk)
            self.assertEqual(ed.sign(sk_b, msg_b).hex(), sig)
            self.assertTrue(ed.verify(bytes.fromhex(pk), msg_b, bytes.fromhex(sig)))
            self.assertFalse(ed.verify(bytes.fromhex(pk), msg_b + b"x", bytes.fromhex(sig)))

    def test_agrees_with_the_cryptography_library_when_it_is_installed(self):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest("cryptography is not installed here")
        for _ in range(5):
            seed, msg = os.urandom(32), os.urandom(40)
            ref = Ed25519PrivateKey.from_private_bytes(seed)
            pub = ref.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            self.assertEqual(ed.public_key(seed), pub)
            self.assertEqual(ed.sign(seed, msg), ref.sign(msg))
            ref.public_key().verify(ed.sign(seed, msg), msg)

    def test_malformed_inputs_are_refused_not_raised(self):
        self.assertFalse(ed.verify(b"short", b"m", b"\0" * 64))
        self.assertFalse(ed.verify(b"\0" * 32, b"m", b"\xff" * 64))
        self.assertFalse(ls.verify({"a": 1, "sig": "zz"}, "00" * 32))


class TestSigningUsesNoCryptographyPackage(unittest.TestCase):
    def test_modules_import_only_the_standard_library(self):
        from pathlib import Path
        for mod in (ed, ls):
            src = Path(mod.__file__).read_text()
            self.assertNotIn("import cryptography", src)
            self.assertNotIn("from cryptography", src)


if __name__ == "__main__":
    unittest.main()
