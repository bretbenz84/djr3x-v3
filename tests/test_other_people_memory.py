"""People other than the speaker: facts about or linked to a mentioned person (October 16 repair).

Synthetic voices and temporary databases only; no audio, models or network.
"""
from r3x.memory import Memory
from tests.test_memory_core import A, B, C, MemoryFixture


class OtherPeopleTests(MemoryFixture):
    def claim(self, subject_id, predicate, value, kind, reference=None, other=None):
        return self.call("record_claim", {"subject_id": subject_id, "subject_reference": reference,
                                          "predicate": predicate, "value": value, "kind": kind,
                                          "source_turn_id": self.state.current[0], "other_person": other})

    def name_only(self, name):
        self.begin("This is " + name + ".")
        result = self.call("introduce_person", {"name": name, "aliases": [], "subject": "other",
                                                "person_id": None, "face_track_id": None})
        self.assertEqual(result.outcome, "ok", result)
        return result.data["person_id"]

    def test_partner_facts_link_to_the_known_person_and_survive_restart(self):
        bret = self.enroll()
        sam = self.name_only("Sam")
        self.begin("Sam Rivera is my partner.")
        partner = self.claim(bret, "has partner", "Sam Rivera", "self_report", other="Sam Rivera")
        self.assertEqual(partner.outcome, "ok", partner)
        self.assertEqual((partner.data["subject_id"], partner.data["status"]), (bret, "accepted"))
        self.assertEqual(partner.data["other_person"], {"person_id": sam, "name": "Sam", "record": "existing"})
        self.assertEqual(self.store.person(sam)["aliases"], ["Sam Rivera"])
        # "He" in the next sentence is the Sam named a moment ago, said by Bret, not Sam's own words.
        self.begin("He doesn't like artificial intelligence.")
        dislike = self.claim(None, "dislikes", "artificial intelligence", "third_party", reference="Sam Rivera")
        self.assertEqual(dislike.outcome, "ok", dislike)
        self.assertEqual((dislike.data["subject_id"], dislike.data["said_by"]), (sam, bret))
        self.store.close()
        self.store = Memory(self.path)
        self.store.start()
        self.state.store = self.store
        self.begin("Do you know Sam Rivera?")
        found = self.call("recall", {"subject_id": None, "query": "Sam Rivera"})
        self.assertEqual(found.outcome, "ok", found)
        person, = found.data["people"]
        self.assertEqual((person["person_id"], person["voice_learned"], person["face_learned"]), (sam, False, False))
        self.assertEqual(person["first_named_in"], "This is Sam.")
        facts = {(f["subject_id"], f["value"], f["said_by"], f["kind"]) for f in person["facts"]}
        self.assertEqual(facts, {(bret, "Sam Rivera", bret, "self_report"),
                                 (sam, "artificial intelligence", bret, "third_party")})
        # Bret's own facts still answer "who is my partner?".
        self.assertIn("has partner", {f["predicate"] for f in self.store.claims(bret)})

    def test_a_new_name_makes_a_name_only_record_without_learning_anyone(self):
        bret = self.enroll()
        self.begin("My sister Kate lives in Denver.")
        result = self.claim(None, "lives in", "Denver", "third_party", reference="Kate")
        self.assertEqual(result.outcome, "ok", result)
        kate = result.data["subject_id"]
        self.assertEqual(self.store.person(kate)["status"], "provisional")
        self.assertIn("name-only", result.data["subject_record"])
        for table in ("recordings", "voice_candidates", "face_references"):
            self.assertEqual(self.store.rows(f"SELECT * FROM {table} WHERE person_id=?", (kate,)), [])
        self.assertFalse(any(e.person_id == kate for e in self.state.enrollments.values()))
        self.assertEqual(self.state.current[4].speaker_id, bret)
        self.assertEqual(self.store.profiles(self.state.tracker.namespace).keys(), {bret})

    def test_a_relationship_with_someone_new_creates_them_in_one_call(self):
        bret = self.enroll()
        self.begin("Sam Rivera is my partner.")
        result = self.claim(bret, "has partner", "Sam Rivera", "self_report", other="Sam Rivera")
        self.assertEqual(result.outcome, "ok", result)
        self.assertEqual(result.data["other_person"]["record"], "new name-only record: no voice or face learned")
        self.assertEqual(self.store.person(result.data["other_person"]["person_id"])["name"], "Sam Rivera")

    def test_unnamed_unheard_stale_and_unclear_names_are_refused(self):
        bret = self.enroll()
        sam = self.name_only("Sam Rivera")
        self.begin("He doesn't like my partner.")
        for reference in ("he", "my partner", "Kate"):
            result = self.claim(None, "dislikes", "AI", "third_party", reference=reference)
            self.assertEqual(result.reason, "MentionNeedsANameSaidInThisConversation", reference)
        self.at += 700   # "Sam Rivera" was said over ten minutes ago.
        self.begin("He doesn't like artificial intelligence.")
        self.assertEqual(self.claim(None, "dislikes", "AI", "third_party", reference="Sam Rivera").reason,
                         "MentionNeedsANameSaidInThisConversation")
        self.begin("Sammy Rivera doesn't like artificial intelligence.")
        self.assertEqual(self.claim(None, "dislikes", "AI", "third_party", reference="Sammy Rivera").reason,
                         "PossiblySameAsAKnownPerson")
        self.begin("Bret likes radios.")
        self.assertEqual(self.claim(None, "likes", "radios", "third_party", reference="Bret").reason,
                         "MentionedPersonIsTheSpeaker")
        other = self.name_only("Sam")
        self.begin("Sam doesn't like artificial intelligence.")
        self.assertEqual(self.claim(None, "dislikes", "AI", "third_party", reference="Sam").reason, "AmbiguousName")
        self.begin("Sam Rivera doesn't like artificial intelligence.")
        self.assertEqual(self.claim(other, "dislikes", "AI", "third_party", reference="Sam Rivera").reason,
                         "NameDoesNotFitThatPerson")
        self.assertEqual(self.store.claims(sam) + self.store.claims(other), [])
        self.assertEqual(len(self.store.people()), 3)

    def test_an_unrecognized_speaker_about_someone_stays_unconfirmed(self):
        self.enroll()
        sam = self.name_only("Sam")
        self.begin("Sam doesn't like artificial intelligence.", C)
        result = self.claim(None, "dislikes", "AI", "third_party", reference="Sam")
        self.assertEqual((result.data["status"], result.data["said_by"]), ("candidate", None))
        self.assertEqual(self.store.claims(sam), [])

    def test_correction_keeps_the_link(self):
        bret = self.enroll()
        sam = self.name_only("Sam")
        self.begin("Sam and I have been together ten years.")
        old = self.claim(bret, "together with partner", "ten years", "self_report", other="Sam").data["claim_id"]
        self.begin("Actually it's eleven years.")
        fixed = self.call("correct_claim", {"claim_id": old, "value": "eleven years", "source_turn_id": self.state.current[0]})
        self.assertEqual(fixed.outcome, "ok", fixed)
        self.assertEqual([c["value"] for c in self.store.claims(sam)], ["eleven years"])

    def test_the_teller_can_forget_someone_known_only_from_them(self):
        bret = self.enroll()
        sam = self.name_only("Sam")
        self.begin("I collect radios.")
        self.claim(bret, "collects", "radios", "self_report")
        self.begin("Sam Rivera is my partner.")
        self.claim(bret, "has partner", "Sam Rivera", "self_report", other="Sam Rivera")
        self.begin("He doesn't like artificial intelligence.")
        self.claim(None, "dislikes", "artificial intelligence", "third_party", reference="Sam")
        self.begin("Please forget Sam.")
        self.assertIn(sam, self.state.identity_targets("forget_person"))
        result = self.call("forget_person", {"person_id": sam})
        self.assertEqual(result.outcome, "ok", result)
        self.assertIsNone(self.store.person(sam))
        self.assertEqual([c["value"] for c in self.store.claims(bret)], ["radios"])
        self.assertEqual(self.store.rows("SELECT * FROM claims WHERE mentions_id IS NOT NULL OR subject_id<>?", (bret,)), [])
        texts = {r["text"] for r in self.store.rows("SELECT text FROM turns")}
        self.assertNotIn("Sam Rivera is my partner.", texts)
        self.assertNotIn("He doesn't like artificial intelligence.", texts)
        self.assertIn("I collect radios.", texts)
        self.assertEqual(self.store.person(bret)["status"], "supported")
        self.assertTrue(self.store.rows("SELECT 1 FROM recordings WHERE person_id=?", (bret,)))

    def test_someone_else_told_about_them_blocks_forgetting_by_voice(self):
        bret = self.enroll()
        jeff = self.enroll("Jeff", B)
        sam = self.name_only("Sam")
        self.begin("Sam doesn't like artificial intelligence.", B)
        self.assertEqual(self.claim(None, "dislikes", "AI", "third_party", reference="Sam").data["said_by"], jeff)
        self.begin("Please forget Sam.", A)
        self.assertEqual(self.state.current[4].speaker_id, bret)
        self.assertNotIn(sam, self.state.identity_targets("forget_person"))
        self.assertEqual(self.call("forget_person", {"person_id": sam}).reason, "SupportedSelfIdentityRequired")
        self.assertIsNotNone(self.store.person(sam))
