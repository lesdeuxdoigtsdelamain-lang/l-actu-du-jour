package uk.family.actudujour

import kotlinx.serialization.encodeToString
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

class ExtrasTest {
    private val now = Instant.parse("2026-11-24T18:00:00Z")
    private val source = ExtraSource("Source officielle", "https://www.gov.uk/news/test")
    private val alert = BreakingAlert(
        "urgent-test-1234", "Une alerte fictive pour les tests", "Données de test uniquement.",
        "2026-11-24T17:00:00Z", "2026-11-24T20:00:00Z", listOf(source)
    )
    private val note = HistoryNote(
        "2026-11-24", "1900-11-24",
        List(6) { "Cette courte phrase appartient à un test technique." }, listOf(source)
    )
    private fun extras() = Extras(1, "2026-11-24T17:59:00Z", listOf(alert), listOf(note))
    private fun withAlert(value: BreakingAlert) = extras().copy(alerts=listOf(value))
    private fun withNote(value: HistoryNote) = extras().copy(history=listOf(value))
    private fun raw(value: Extras) = wire.encodeToString(value).encodeToByteArray()
    private fun rejects(value: Extras) {
        assertThrows(RuntimeException::class.java) { parseExtras(raw(value), now) }
    }

    @Test fun parsesSeparateSidecarAndKeepsLegacyEditionContractStrict() {
        assertEquals(extras(), parseExtras(raw(extras()), now))
        // The sidecar must never be accepted as an edition or alter its schema.
        assertThrows(RuntimeException::class.java) { parseEdition(raw(extras())) }
        assertThrows(RuntimeException::class.java) {
            parseExtras(raw(extras()).decodeToString().replace(
                "\"schema_version\":1", "\"schema_version\":1,\"unexpected\":true").encodeToByteArray(), now)
        }
    }

    @Test fun filtersExpiredAndFutureAlertsAtDisplayTimeWithoutAnotherDownload() {
        val state = validExtras(extras(), now)
        assertEquals(listOf(alert), activeAlerts(state, now))
        assertTrue(activeAlerts(state, Instant.parse("2026-11-24T20:00:00Z")).isEmpty())
        assertTrue(activeAlerts(state, Instant.parse("2026-11-24T16:59:59Z")).isEmpty())
        assertTrue(activeAlerts(null, now).isEmpty())
    }

    @Test fun neverSubstitutesTodaysHistoryForAnArchiveAndRetainsSevenDays() {
        val yesterday = note.copy(editionDate="2026-11-23", eventDate="1900-11-23")
        val state = extras().copy(history=listOf(note,yesterday))
        assertEquals(note, historyFor(state,"2026-11-24",LocalDate.parse("2026-11-24")))
        assertEquals(yesterday, historyFor(state,"2026-11-23",LocalDate.parse("2026-11-24")))
        assertNull(historyFor(state,"2026-11-22",LocalDate.parse("2026-11-24")))
        assertNull(historyFor(state,"2026-11-24",LocalDate.parse("2026-12-01")))
        assertNull(historyFor(state,"2026-11-24",LocalDate.parse("2026-11-23")))
    }

    @Test fun acceptsEmptySidecarWithoutRemovingTheJournal() {
        val empty = extras().copy(alerts=emptyList(),history=emptyList())
        assertEquals(empty, parseExtras(raw(empty),now))
    }

    @Test fun rejectsInvalidOrFutureHistoryDatesAndNonMatchingAnniversaries() {
        for (invalid in listOf(
            note.copy(eventDate="1900-11-23"),
            note.copy(eventDate="2026-11-24"),
            note.copy(eventDate="2030-11-24"),
            note.copy(eventDate="1900-02-30"),
            note.copy(eventDate="1900-1-24"),
            note.copy(editionDate="2026-11-25",eventDate="1900-11-25"),
            note.copy(editionDate="2026-11-24T00:00:00Z")
        )) rejects(withNote(invalid))
    }

    @Test fun rejectsUnsafeSourcesMarkupAndUnboundedText() {
        rejects(withAlert(alert.copy(sources=listOf(source.copy(url="http://www.gov.uk/news/test")))))
        rejects(withNote(note.copy(sources=listOf(source.copy(url="https://localhost/secret")))))
        rejects(withAlert(alert.copy(title="<b>Une alerte fictive</b>")))
        rejects(withAlert(alert.copy(summary="x".repeat(401))))
        rejects(withNote(note.copy(lines=List(6) { "x".repeat(241) })))
        rejects(withNote(note.copy(lines=List(6) { "Une phrase\net une autre." })))
        rejects(withNote(note.copy(sources=emptyList())))
    }

    @Test fun enforcesCardCountsUniqueIdsAndSixToTenHistoryLines() {
        rejects(extras().copy(alerts=List(4) { alert.copy(id="unique-test-123$it") }))
        rejects(extras().copy(alerts=listOf(alert,alert)))
        rejects(extras().copy(history=listOf(note,note)))
        rejects(extras().copy(history=List(8) { index ->
            val day=LocalDate.parse(note.editionDate).minusDays(index.toLong())
            note.copy(editionDate=day.toString(),eventDate=day.withYear(1900).toString())
        }))
        rejects(withNote(note.copy(lines=List(5) { "Une phrase." })))
        rejects(withNote(note.copy(lines=List(11) { "Une phrase." })))
        val tenLines=withNote(note.copy(lines=List(10) { "Une phrase." }))
        assertEquals(10,parseExtras(raw(tenLines),now).history.single().lines.size)
    }

    @Test fun rejectsInvalidTimestampsAndUnboundedLifetimes() {
        rejects(extras().copy(updatedAt="2026-11-24T18:00:00"))
        rejects(extras().copy(updatedAt="2026-11-24T18:06:00Z"))
        rejects(withAlert(alert.copy(publishedAt="2026-11-24T17:00:00")))
        rejects(withAlert(alert.copy(expiresAt=alert.publishedAt)))
        rejects(withAlert(alert.copy(expiresAt="2026-11-24T20:00:01Z")))
        rejects(withAlert(alert.copy(expiresAt="2026-11-26T17:00:00Z")))
        rejects(extras().copy(schemaVersion=2))
    }
}
