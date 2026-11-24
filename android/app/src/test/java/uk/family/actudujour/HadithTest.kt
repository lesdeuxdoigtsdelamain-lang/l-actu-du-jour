package uk.family.actudujour

import kotlinx.serialization.encodeToString
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

class HadithTest {
    private val now=Instant.parse("2026-11-24T18:00:00Z")
    private val source=HadithSource("Site officiel de test", "Savant de test",
        "https://binbaz.org.sa/test/fixture", "ar")
    // Deliberately non-religious fixtures, never presented as a genuine hadith.
    private val note=HadithNote("hadith-test-1234", "2026-11-24", "نص عربي للاختبار فقط",
        "راو للاختبار", "مرجع للاختبار", "Authentique — indication technique fictive",
        "Texte français uniquement destiné au test technique.",
        "Explication fictive pour vérifier le format des données sans publier de contenu religieux.",
        listOf(source))
    private fun feed()=HadithFeed(1,"2026-11-24T17:59:00Z",listOf(note))
    private fun raw(value:HadithFeed)=wire.encodeToString(value).encodeToByteArray()
    private fun rejects(value:HadithFeed) {
        assertThrows(RuntimeException::class.java) { parseHadith(raw(value),now) }
    }

    @Test fun acceptsIndependentStrictSidecarWithoutChangingExistingJsonContracts() {
        assertEquals(feed(),parseHadith(raw(feed()),now))
        assertThrows(RuntimeException::class.java) { parseExtras(raw(feed()),now) }
        assertThrows(RuntimeException::class.java) { parseEdition(raw(feed())) }
        assertThrows(RuntimeException::class.java) {
            parseHadith(raw(feed()).decodeToString().replace("\"schema_version\":1",
                "\"schema_version\":1,\"unreviewed\":true").encodeToByteArray(),now)
        }
    }

    @Test fun restrictsEverySourceToTheReviewedOfficialArabicSite() {
        for(url in listOf("http://binbaz.org.sa/test", "https://unreviewed.example/test",
            "https://binbaz.org.sa.evil.example/test", "https://binbaz.org.sa@evil.example/test",
            "https://other.binbaz.org.sa/test", "https://binbaz.org.sa:8443/test",
            "https://binbaz.org.sa/")) {
            rejects(feed().copy(hadiths=listOf(note.copy(sources=listOf(source.copy(url=url))))))
        }
        rejects(feed().copy(hadiths=listOf(note.copy(sources=listOf(source.copy(language="fr"))))))
        rejects(feed().copy(hadiths=listOf(note.copy(sources=emptyList()))))
        rejects(feed().copy(hadiths=listOf(note.copy(sources=listOf(source,source)))))
    }

    @Test fun requiresArabicOriginalNarratorReferenceAndBoundedReadableFrench() {
        for(invalid in listOf(note.copy(arabic="A French text"),note.copy(arabic="١٢٣٤"),
            note.copy(narratorAr="A narrator"),note.copy(referenceAr="A reference"),
            note.copy(arabic="<b>نص عربي</b>"),note.copy(translationFr="Une ligne\nune autre"),
            note.copy(translationFr="Texte avec \u202e contrôle invisible"),
            note.copy(authenticityFr="Statut d'authenticité inconnu"),
            note.copy(arabic="ع".repeat(1601)),note.copy(translationFr="x".repeat(1601)),
            note.copy(explanationFr=List(101){"mot"}.joinToString(" ")),
            note.copy(id="unexpected path!"))) rejects(feed().copy(hadiths=listOf(invalid)))
    }

    @Test fun retainsSevenDistinctCalendarDatesAndRejectsFutureOrMalformedDates() {
        val seven=feed().copy(hadiths=List(7) { offset ->
            note.copy(editionDate=LocalDate.parse(note.editionDate).minusDays(offset.toLong()).toString())
        })
        assertEquals(7,parseHadith(raw(seven),now).hadiths.size)
        rejects(feed().copy(hadiths=emptyList()))
        rejects(feed().copy(hadiths=listOf(note,note)))
        for(day in listOf("2026-11-17","2026-11-25","2026-2-24","2026-02-30")) {
            rejects(feed().copy(hadiths=listOf(note.copy(editionDate=day))))
        }
        rejects(feed().copy(updatedAt="2026-11-24T18:06:00Z"))
        rejects(feed().copy(updatedAt="2026-11-24T18:00:00"))
        rejects(feed().copy(schemaVersion=2))
    }

    @Test fun usesLondonCalendarAtSummerMidnight() {
        val summerNow=Instant.parse("2026-07-01T23:30:00Z")
        val summerFeed=feed().copy(updatedAt="2026-07-01T23:29:00Z",
            hadiths=listOf(note.copy(editionDate="2026-07-02")))
        assertEquals("2026-07-02",parseHadith(raw(summerFeed),summerNow).hadiths.single().editionDate)
    }

    @Test fun midnightNeverRelabelsYesterdaysCacheAsTodaysHadith() {
        val today=LocalDate.parse("2026-11-24")
        assertEquals(note,hadithFor(feed(),today.toString(),today))
        assertNull(hadithFor(feed(),"2026-11-25",today.plusDays(1)))
        assertEquals(note,hadithFor(feed(),today.toString(),today.plusDays(1)))
        assertNull(hadithFor(feed(),today.toString(),today.plusDays(7)))
        assertNull(hadithFor(feed(),today.toString(),today.minusDays(1)))
        assertNull(hadithFor(null,today.toString(),today))
    }

    @Test fun defaultFeedUsesTodaysSupplementWhileAnArchiveKeepsItsOwnDay() {
        val today=LocalDate.parse("2026-11-24")
        assertEquals("2026-11-24",supplementDate(null,"2026-11-23",today))
        assertEquals("2026-11-23",supplementDate("2026-11-23","2026-11-23",today))
        assertEquals("2026-11-24",supplementDate("2026-11-15","2026-11-23",today))
        assertEquals("2026-11-24",supplementDate(null,null,today))
    }
}
