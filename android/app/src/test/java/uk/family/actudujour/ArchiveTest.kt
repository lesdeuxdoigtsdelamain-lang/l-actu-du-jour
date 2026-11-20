package uk.family.actudujour

import kotlinx.serialization.encodeToString
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate

class ArchiveTest {
    private val today = LocalDate.parse("2026-11-20")
    private fun manifest(day: String, checksum: String = "a".repeat(64)) = Latest(
        1, day, "editions/$day.json", checksum, "2026-11-20T20:00:00Z"
    )
    private fun index(vararg entries: Latest): ByteArray =
        wire.encodeToString(Archive(1, entries.toList())).encodeToByteArray()

    @Test fun receivesCorrectionsEvenWhenTheLatestDayIsUnchanged() {
        val latest = manifest("2026-11-20")
        val corrected = manifest("2026-11-19", "b".repeat(64))
        val results = archiveManifests(index(latest, corrected), latest, today)
        assertEquals(listOf(corrected), results)
        assertEquals("b".repeat(64), results.single().sha256)
    }

    @Test fun archiveCannotOverrideLatestOrRequestAnUnpublishedDay() {
        val latest = manifest("2026-11-19")
        val results = archiveManifests(
            index(manifest("2026-11-20"), manifest("2026-11-19", "b".repeat(64)), manifest("2026-11-18")),
            latest, today
        )
        assertEquals(listOf("2026-11-18"), results.map { it.date })
    }

    @Test fun retentionUsesTodaysDateWhenTheLastPublicationWasYesterday() {
        val latest = manifest("2026-11-19")
        val results = archiveManifests(
            index(manifest("2026-11-13"), manifest("2026-11-14"), manifest("2026-11-18")), latest, today
        )
        assertEquals(listOf("2026-11-18", "2026-11-14"), results.map { it.date })
    }

    @Test fun refusesUnboundedAndRepeatedManifestEntries() {
        assertThrows(IllegalArgumentException::class.java) {
            archiveManifests(index(*Array(8) { manifest(today.minusDays(it.toLong()).toString()) }), manifest("2026-11-20"), today)
        }
        assertThrows(IllegalArgumentException::class.java) {
            archiveManifests(index(manifest("2026-11-19"), manifest("2026-11-19")), manifest("2026-11-20"), today)
        }
    }

    @Test fun refusesUnsafePathsFutureDatesAndInvalidHashes() {
        for (invalid in listOf(
            manifest("2026-11-19").copy(path = "../settings.json"),
            manifest("2026-11-19").copy(sha256 = "invalid"),
            manifest("2026-11-21"),
            manifest("2026-11-19").copy(schemaVersion = 2)
        )) {
            assertThrows(IllegalArgumentException::class.java) { validateManifest(invalid, today) }
        }
    }

    @Test fun validatesEveryDownloadedEditionBeforeReplacement() {
        val paragraph = "Ce paragraphe appartient exclusivement à un exemple technique sans actualité réelle. ".repeat(7).trim()
        val paragraphs = List(4) { paragraph }
        val article = Article(
            "test-archive-1234", "Monde", "Un article fictif pour les tests techniques", paragraphs, null,
            listOf(SourceRef("Source de test", "https://www.gov.uk/news/test", "en", "2026-11-19T10:00:00Z")),
            paragraphs.joinToString(" ").split(Regex("\\s+")).size, 2
        )
        val edition = Edition(1, "2026-11-19-fr-FR", "2026-11-19", "2026-11-19T18:00:00Z",
            "fr-FR", "Europe/London", "partielle", 2, listOf(article))
        val raw = wire.encodeToString(edition).encodeToByteArray()
        val correct = manifest("2026-11-19", sha(raw))
        assertEquals(edition, validateDownload(raw, correct, today))
        assertThrows(IllegalArgumentException::class.java) {
            validateDownload(raw + " ".encodeToByteArray(), correct, today)
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateDownload(raw, manifest("2026-11-18", sha(raw)), today)
        }
    }
}
