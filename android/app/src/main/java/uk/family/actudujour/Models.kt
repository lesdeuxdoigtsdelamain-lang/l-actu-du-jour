package uk.family.actudujour

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.net.URI
import java.security.MessageDigest
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId

val wire = Json { ignoreUnknownKeys = false; coerceInputValues = false }
val london: ZoneId = ZoneId.of("Europe/London")
fun ukToday(): LocalDate = LocalDate.now(london)

@Serializable data class SourceRef(
    val publisher: String,
    val url: String,
    val language: String,
    @SerialName("published_at") val publishedAt: String
)
@Serializable data class CoverImage(val url: String, val alt: String, val credit: String,
    val license: String, val page: String)
@Serializable data class Article(
    val id: String, val category: String, val title: String,
    val paragraphs: List<String>, val image: CoverImage? = null,
    @SerialName("source_refs") val sources: List<SourceRef>,
    @SerialName("word_count") val wordCount: Int,
    val minutes: Int
)
@Serializable data class Edition(
    @SerialName("schema_version") val schemaVersion: Int,
    @SerialName("edition_id") val id: String,
    @SerialName("edition_date") val date: String,
    @SerialName("generated_at") val generatedAt: String,
    val language: String, val timezone: String, val status: String,
    val minutes: Int, val articles: List<Article>
)
@Serializable data class Latest(
    @SerialName("schema_version") val schemaVersion: Int,
    val date: String, val path: String, val sha256: String,
    @SerialName("published_at") val publishedAt: String
)
@Serializable data class Archive(
    @SerialName("schema_version") val schemaVersion: Int,
    val editions: List<Latest>
)

fun validateManifest(manifest: Latest, today: LocalDate = ukToday()): LocalDate {
    require(manifest.schemaVersion == 1 && manifest.sha256.matches(Regex("[a-f0-9]{64}")))
    val day = LocalDate.parse(manifest.date)
    require(manifest.date == day.toString() && manifest.path == "editions/$day.json")
    require(!day.isAfter(today)) { "Édition datée dans le futur" }
    OffsetDateTime.parse(manifest.publishedAt)
    return day
}

/** The optional index never overrides the authoritative latest manifest. */
fun archiveManifests(raw: ByteArray, latest: Latest, today: LocalDate = ukToday()): List<Latest> {
    val archive = wire.decodeFromString<Archive>(raw.decodeToString())
    require(archive.schemaVersion == 1 && archive.editions.size <= 7)
    require(archive.editions.map { it.date }.distinct().size == archive.editions.size)
    val latestDay = validateManifest(latest, today)
    val cutoff = today.minusDays(6)
    return archive.editions.filter { manifest ->
        val day = validateManifest(manifest, today)
        // The last publication can still be yesterday while the next is prepared.
        // Ignore entries that have aged out rather than rejecting the entire index.
        !day.isBefore(cutoff) && day.isBefore(latestDay)
    }.sortedByDescending { it.date }
}

fun validateDownload(raw: ByteArray, manifest: Latest, today: LocalDate = ukToday()): Edition {
    validateManifest(manifest, today)
    require(sha(raw) == manifest.sha256) { "Signature d'intégrité du journal incorrecte" }
    val edition = parseEdition(raw)
    require(edition.date == manifest.date) { "Date du journal incorrecte" }
    return edition
}

fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
    .digest(bytes).joinToString("") { "%02x".format(it) }
fun safeUrl(url: String): Boolean = runCatching {
    val u = URI(url)
    u.scheme == "https" && !u.host.isNullOrBlank() && u.userInfo == null &&
        u.port in listOf(-1,443) && !u.host.matches(Regex("[0-9.:]+")) &&
        u.host.lowercase() !in setOf("localhost", "localhost.localdomain") &&
        !u.host.lowercase().endsWith(".localhost") && !u.host.lowercase().endsWith(".local")
}.getOrDefault(false)
fun valid(e: Edition): Edition {
    require(e.schemaVersion == 1 && e.language == "fr-FR" && e.timezone == "Europe/London")
    val date = LocalDate.parse(e.date)
    require(e.id == "$date-fr-FR" && e.status in listOf("complete","partielle"))
    OffsetDateTime.parse(e.generatedAt)
    require(e.articles.size in 1..8 && e.articles.map { it.id }.distinct().size == e.articles.size)
    if(e.status == "complete") require(e.articles.size >= 6)
    require(e.minutes == e.articles.sumOf { it.minutes })
    for(a in e.articles) {
        require(a.id.length in 12..64 && a.title.length in 18..180 && a.sources.isNotEmpty())
        require(a.paragraphs.size in 4..13 && a.wordCount in 280..780)
        require(a.paragraphs.joinToString(" ").trim().split(Regex("[\\s\\p{Z}]+")).size == a.wordCount)
        require(a.paragraphs.all { it.split(Regex("[\\s\\p{Z}]+")).size in 18..160 && '<' !in it && '>' !in it })
        require(a.sources.size <= 5 && a.sources.all { safeUrl(it.url) })
        a.image?.let { require(safeUrl(it.url) && URI(it.url).host == "upload.wikimedia.org") }
    }
    return e
}
fun parseEdition(raw: ByteArray): Edition = valid(wire.decodeFromString(raw.decodeToString()))
fun fallback(category: String): Int = when(category) {
    "Maroc" -> R.drawable.cover_maroc
    "Monde musulman" -> R.drawable.cover_musulman
    "Royaume-Uni" -> R.drawable.cover_royaume_uni
    "France" -> R.drawable.cover_france
    "Économie" -> R.drawable.cover_economie
    "Science et technologie" -> R.drawable.cover_science
    "Santé" -> R.drawable.cover_sante
    "Société" -> R.drawable.cover_societe
    "Environnement" -> R.drawable.cover_environnement
    else -> R.drawable.cover_monde
}
