package uk.family.actudujour

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.net.URI
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime

/** Independently cached: older editions and optional supplements keep their own contracts. */
@Serializable data class HadithSource(
    val publisher: String,
    val scholar: String,
    val url: String,
    val language: String
)
@Serializable data class HadithNote(
    val id: String,
    @SerialName("edition_date") val editionDate: String,
    val arabic: String,
    @SerialName("narrator_ar") val narratorAr: String,
    @SerialName("reference_ar") val referenceAr: String,
    @SerialName("authenticity_fr") val authenticityFr: String,
    @SerialName("translation_fr") val translationFr: String,
    @SerialName("explanation_fr") val explanationFr: String,
    val sources: List<HadithSource>
)
@Serializable data class HadithFeed(
    @SerialName("schema_version") val schemaVersion: Int,
    @SerialName("updated_at") val updatedAt: String,
    val hadiths: List<HadithNote>
)

private fun hadithText(value: String, minimum: Int, maximum: Int): Boolean =
    value.length in minimum..maximum && value == value.trim() &&
        value.none { it == '<' || it == '>' || it.isISOControl() || it.category == CharCategory.FORMAT }

private fun arabicText(value: String, minimum: Int, maximum: Int): Boolean =
    hadithText(value, minimum, maximum) && value.any {
        it.isLetter() && Character.UnicodeScript.of(it.code) == Character.UnicodeScript.ARABIC
    }

private fun hadithDate(value: String): LocalDate {
    require(value.matches(Regex("\\d{4}-\\d{2}-\\d{2}")))
    val day = LocalDate.parse(value)
    require(day.year in 1..9999 && day.toString() == value)
    return day
}

fun validHadith(feed: HadithFeed, now: Instant = Instant.now()): HadithFeed {
    require(feed.schemaVersion == 1)
    val updated = OffsetDateTime.parse(feed.updatedAt).toInstant()
    require(!updated.isAfter(now.plusSeconds(300))) { "Hadith daté dans le futur" }
    val publicationDay = updated.atZone(london).toLocalDate()
    val today = now.atZone(london).toLocalDate()
    require(feed.hadiths.size in 1..7)
    require(feed.hadiths.map { it.editionDate }.distinct().size == feed.hadiths.size)
    for (note in feed.hadiths) {
        val day = hadithDate(note.editionDate)
        require(!day.isAfter(today) && !day.isAfter(publicationDay) &&
            !day.isBefore(publicationDay.minusDays(6)))
        require(note.id.matches(Regex("[a-z0-9_-]{12,64}")))
        require(arabicText(note.arabic, 1, 1600))
        require(arabicText(note.narratorAr, 2, 240))
        require(arabicText(note.referenceAr, 2, 500))
        require(hadithText(note.authenticityFr, 10, 240) && note.authenticityFr.startsWith("Authentique"))
        require(hadithText(note.translationFr, 5, 1600))
        require(hadithText(note.explanationFr, 10, 1000) &&
            note.explanationFr.split(Regex("[\\s\\p{Z}]+")).size <= 100)
        require(note.sources.size in 1..3 &&
            note.sources.map { it.url }.distinct().size == note.sources.size)
        for (source in note.sources) {
            require(hadithText(source.publisher, 2, 90) && hadithText(source.scholar, 2, 120))
            require(source.language == "ar" && source.url.length <= 2048 && safeUrl(source.url))
            // A reviewed scholarly source, rather than a general-purpose religious search result.
            val address=URI(source.url)
            require(address.host == "binbaz.org.sa" && !address.path.isNullOrBlank() && address.path != "/")
        }
    }
    return feed
}

fun parseHadith(raw: ByteArray, now: Instant = Instant.now()): HadithFeed =
    validHadith(wire.decodeFromString<HadithFeed>(raw.decodeToString()), now)

/** Never relabel an old cached hadith as today's; archive notes keep their own day. */
fun hadithFor(feed: HadithFeed?, date: String, today: LocalDate = ukToday()): HadithNote? {
    val chosen = runCatching { hadithDate(date) }.getOrNull() ?: return null
    if (chosen.isAfter(today) || chosen.isBefore(today.minusDays(6))) return null
    return feed?.hadiths?.firstOrNull { it.editionDate == date }
}

/** The default feed follows the calendar, even before today's news edition is ready. */
fun supplementDate(selectedArchive: String?, editionDate: String?, today: LocalDate): String =
    selectedArchive?.takeIf { it == editionDate } ?: today.toString()
