package uk.family.actudujour

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.decodeFromString
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayOutputStream
import java.io.File
import java.time.LocalDate
import java.util.concurrent.TimeUnit

class NewsRepository(private val context: Context) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(8,TimeUnit.SECONDS).readTimeout(17,TimeUnit.SECONDS)
        .followRedirects(false).build()
    private val folder = File(context.filesDir,"editions").apply { mkdirs() }
    private val settings = context.getSharedPreferences("reader",Context.MODE_PRIVATE)
    val editions = MutableStateFlow(loadLocal())
    val readIds = MutableStateFlow(settings.getStringSet("read",emptySet())?.toSet() ?: emptySet())

    private fun download(path: String, maximum: Long): ByteArray {
        val url = BuildConfig.PAGES_URL + path
        require(safeUrl(url) && url.startsWith(BuildConfig.PAGES_URL))
        val req=Request.Builder().url(url).header("Cache-Control","no-cache").get().build()
        client.newCall(req).execute().use { response ->
            require(response.isSuccessful) { "Serveur indisponible (${response.code})" }
            val body = response.body ?: error("Réponse vide")
            require(body.contentLength() <= maximum) { "Données trop volumineuses" }
            body.byteStream().use { input ->
                val result = ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    require(result.size().toLong() + count <= maximum) { "Données trop volumineuses" }
                    result.write(buffer, 0, count)
                }
                return result.toByteArray()
            }
        }
    }
    private fun loadLocal(): List<Edition> = folder.listFiles { f ->
        f.name.matches(Regex("\\d{4}-\\d{2}-\\d{2}\\.json"))
    }?.mapNotNull { f -> runCatching { parseEdition(f.readBytes()) }.getOrNull() }
        ?.sortedByDescending { it.date } ?: emptyList()

    private fun syncEdition(manifest: Latest, today: LocalDate): Boolean {
        val day = validateManifest(manifest, today)
        val existing=File(folder,"$day.json")
        val old=if(existing.exists())runCatching { existing.readBytes() }.getOrNull() else null
        if(old != null && sha(old)==manifest.sha256) return false
        val newData=download(manifest.path,800000)
        validateDownload(newData, manifest, today)
        // Validate before touching a healthy saved edition. Unique temporary names
        // also avoid sharing a partially written file with the background worker.
        val tmp=File.createTempFile("$day-", ".tmp", folder)
        try {
            tmp.writeBytes(newData)
            require(tmp.renameTo(existing)) { "Échec d'enregistrement local" }
        } finally {
            tmp.delete()
        }
        return true
    }

    suspend fun refresh(): Boolean = withContext(Dispatchers.IO) {
        val today = ukToday()
        val manifest=wire.decodeFromString<Latest>(download("latest.json",20000).decodeToString())
        val latestChanged = syncEdition(manifest, today)
        // Display the latest successful download even if an archive request fails.
        editions.value=loadLocal()
        val archive = runCatching {
            archiveManifests(download("archive.json",20000), manifest, today)
        }.getOrDefault(emptyList())
        for (entry in archive) {
            // One missing, corrupt or offline archive must not hide the latest
            // edition or prevent other previously saved days from being corrected.
            runCatching { syncEdition(entry, today) }
        }
        val cutoff=today.minusDays(6)
        folder.listFiles()?.forEach { file ->
            val day=runCatching { LocalDate.parse(file.name.removeSuffix(".json")) }.getOrNull()
            if(day!=null && day.isBefore(cutoff)) file.delete()
        }
        editions.value=loadLocal()
        // Old corrections do not announce a new daily edition to the worker.
        latestChanged
    }

    fun toggleRead(id: String) {
        val next=readIds.value.toMutableSet()
        if(!next.add(id))next.remove(id)
        readIds.value=next
        settings.edit().putStringSet("read",next).apply()
    }
    fun theme() = settings.getString("theme","Clair") ?: "Clair"
    fun fontSize() = settings.getFloat("font",1.0f)
    fun saveTheme(value:String) { settings.edit().putString("theme",value).apply() }
    fun saveFont(value:Float) { settings.edit().putFloat("font",value).apply() }
}
