package uk.family.actudujour

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime

/** Optional sidecar: the edition contract stays readable by earlier releases. */
@Serializable data class ExtraSource(val publisher: String, val url: String)
@Serializable data class BreakingAlert(
    val id: String,
    val title: String,
    val summary: String,
    @SerialName("published_at") val publishedAt: String,
    @SerialName("expires_at") val expiresAt: String,
    val sources: List<ExtraSource>
)
@Serializable data class HistoryNote(
    @SerialName("edition_date") val editionDate: String,
    @SerialName("event_date") val eventDate: String,
    val lines: List<String>,
    val sources: List<ExtraSource>
)
@Serializable data class Extras(
    @SerialName("schema_version") val schemaVersion: Int,
    @SerialName("updated_at") val updatedAt: String,
    val alerts: List<BreakingAlert>,
    val history: List<HistoryNote>
)

private fun plainText(value: String, minimum: Int, maximum: Int): Boolean =
    value.length in minimum..maximum && value == value.trim() &&
        value.none { it == '<' || it == '>' || it.isISOControl() }

private fun extraDate(value: String): LocalDate {
    require(value.matches(Regex("\\d{4}-\\d{2}-\\d{2}")))
    val day = LocalDate.parse(value)
    require(day.year in 1..9999 && day.toString() == value)
    return day
}

private fun validSources(sources: List<ExtraSource>) {
    require(sources.size in 1..5 && sources.map { it.url }.distinct().size == sources.size)
    require(sources.all { plainText(it.publisher, 2, 90) && it.url.length <= 2048 && safeUrl(it.url) })
}

fun validExtras(extras: Extras, now: Instant = Instant.now()): Extras {
    require(extras.schemaVersion == 1)
    val updated = OffsetDateTime.parse(extras.updatedAt).toInstant()
    require(!updated.isAfter(now.plusSeconds(300))) { "Compléments datés dans le futur" }
    require(extras.alerts.size <= 3 && extras.alerts.map { it.id }.distinct().size == extras.alerts.size)
    for (alert in extras.alerts) {
        require(plainText(alert.id, 12, 64) && plainText(alert.title, 12, 200))
        require(plainText(alert.summary, 0, 400))
        val published = OffsetDateTime.parse(alert.publishedAt).toInstant()
        val expires = OffsetDateTime.parse(alert.expiresAt).toInstant()
        require(expires.isAfter(published) && Duration.between(published, expires) <= Duration.ofHours(3))
        validSources(alert.sources)
    }
    require(extras.history.size <= 7 && extras.history.map { it.editionDate }.distinct().size == extras.history.size)
    val today = now.atZone(london).toLocalDate()
    for (note in extras.history) {
        val edition = extraDate(note.editionDate)
        val event = extraDate(note.eventDate)
        require(!edition.isAfter(today) && event.isBefore(edition))
        require(event.month == edition.month && event.dayOfMonth == edition.dayOfMonth)
        require(note.lines.size in 6..10 && note.lines.all { plainText(it, 1, 240) })
        validSources(note.sources)
    }
    return extras
}

fun parseExtras(raw: ByteArray, now: Instant = Instant.now()): Extras =
    validExtras(wire.decodeFromString<Extras>(raw.decodeToString()), now)

/** Apply time filtering at display time, including when the phone is offline. */
fun activeAlerts(extras: Extras?, now: Instant = Instant.now()): List<BreakingAlert> =
    extras?.alerts?.filter {
        !OffsetDateTime.parse(it.publishedAt).toInstant().isAfter(now) &&
            OffsetDateTime.parse(it.expiresAt).toInstant().isAfter(now)
    }.orEmpty().sortedByDescending { OffsetDateTime.parse(it.publishedAt).toInstant() }

fun historyFor(extras: Extras?, date: String, today: LocalDate = ukToday()): HistoryNote? {
    val chosen = runCatching { extraDate(date) }.getOrNull() ?: return null
    if (chosen.isAfter(today) || chosen.isBefore(today.minusDays(6))) return null
    return extras?.history?.firstOrNull { it.editionDate == date }
}
