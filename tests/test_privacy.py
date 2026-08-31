import unittest

from hoistway_audit.privacy import argument_digest, simhash64, simhash_similarity, url_set_digest


class PrivacySignalsTests(unittest.TestCase):
    def test_simhash_is_fixed_width_and_tolerates_small_metadata_changes(self):
        left = simhash64({"answer": "Dublin is mild and rainy today", "request_id": "one"})
        right = simhash64({"answer": "Dublin is mild and rainy today", "request_id": "two"})
        self.assertGreaterEqual(left, -(1 << 63))
        self.assertLess(left, 1 << 63)
        self.assertGreater(simhash_similarity(left, right), 0.8)

    def test_url_set_digest_ignores_json_order_but_not_result_membership(self):
        first = {"results": [{"url": "https://a.test/1"}, {"url": "https://b.test/2"}]}
        reordered = {"results": [{"url": "https://b.test/2"}, {"url": "https://a.test/1"}]}
        changed = {"results": [{"url": "https://a.test/1"}, {"url": "https://c.test/3"}]}
        self.assertEqual(url_set_digest("secret", first), url_set_digest("secret", reordered))
        self.assertNotEqual(url_set_digest("secret", first), url_set_digest("secret", changed))

    def test_argument_canonicalisation_drops_only_declared_noise(self):
        noisy = {
            "query": "agent   runtime",
            "request_id": "one-off",
            "url": "HTTPS://WWW.Example.COM/search?utm_source=newsletter&b=2&a=1#section",
        }
        clean = {
            "url": "https://example.com/search?a=1&b=2",
            "query": "agent runtime",
        }
        changed = {"url": "https://example.com/search?a=1&b=3", "query": "agent runtime"}
        self.assertEqual(argument_digest("secret", noisy), argument_digest("secret", clean))
        self.assertNotEqual(argument_digest("secret", noisy), argument_digest("secret", changed))


if __name__ == "__main__":
    unittest.main()
