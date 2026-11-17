package uk.family.actudujour

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.decodeFromString
import okhttp3.OkHttpClient
import okhttp3.Request
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
            val bytes=body.bytes()
            require(bytes.size.toLong()<=maximum) { "Données trop volumineuses" }
            return bytes
        }
    }
    private fun loadLocal(): List<Edition> = folder.listFiles { f ->
        f.name.matches(Regex("\\d{4}-\\d{2}-\\d{2}\\.json"))
    }?.mapNotNull { f -> runCatching { parseEdition(f.readBytes()) }.getOrNull() }
        ?.sortedByDescending { it.date } ?: emptyList()

    suspend fun refresh(): Boolean = withContext(Dispatchers.IO) {
        val manifest=wire.decodeFromString<Latest>(download("latest.json",20000).decodeToString())
        require(manifest.schemaVersion==1 && manifest.sha256.matches(Regex("[a-f0-9]{64}")))
        require(manifest.path=="editions/${manifest.date}.json")
        val d=LocalDate.parse(manifest.date)
        require(!d.isAfter(ukToday())) { "Édition datée dans le futur" }
        val existing=File(folder,"${d}.json")
        val old=if(existing.exists())runCatching { existing.readBytes() }.getOrNull() else null
        if(old != null && sha(old)==manifest.sha256) {
            editions.value=loadLocal();return@withContext false
        }
        val newData=download(manifest.path,800000)
        require(sha(newData)==manifest.sha256) { "Signature d'intégrité du journal incorrecte" }
        val e=parseEdition(newData)
        require(e.date==manifest.date)
        val tmp=File(folder,"${d}.tmp");tmp.writeBytes(newData)
        require(tmp.renameTo(existing)) { "Échec d'enregistrement local" }
        val cutoff=ukToday().minusDays(6)
        folder.listFiles()?.forEach { file ->
            val day=runCatching { LocalDate.parse(file.name.removeSuffix(".json")) }.getOrNull()
            if(day!=null && day.isBefore(cutoff)) file.delete()
        }
        editions.value=loadLocal()
        true
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
