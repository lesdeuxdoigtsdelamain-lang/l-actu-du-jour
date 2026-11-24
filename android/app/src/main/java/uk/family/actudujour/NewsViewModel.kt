package uk.family.actudujour

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.CancellationException
import java.time.Instant
import androidx.compose.runtime.mutableStateOf

class NewsViewModel(app: Application):AndroidViewModel(app) {
    val repo=NewsRepository(app)
    val editions=repo.editions
    val extras=repo.extras
    val clock=mutableStateOf(Instant.now())
    private var refreshingExtras=false
    val readIds=repo.readIds
    val working=mutableStateOf(false)
    val message=mutableStateOf("")
    val theme=mutableStateOf(repo.theme())
    val font=mutableStateOf(repo.fontSize())
    fun tick() { clock.value=Instant.now() }
    fun refreshExtras() {
        tick()
        if(refreshingExtras)return
        refreshingExtras=true
        viewModelScope.launch {
            try { repo.refreshExtras() }
            catch(e:CancellationException) { throw e }
            catch(_:Exception) { /* Keep validated cache; expired alerts are hidden by the display filter. */ }
            finally { refreshingExtras=false;tick() }
        }
    }
    fun refresh() {
        refreshExtras()
        if(working.value)return
        working.value=true
        viewModelScope.launch {
            try {
                val changed=repo.refresh()
                message.value=if(changed)"Nouvelle édition téléchargée" else "Journal à jour"
            } catch(e:CancellationException) {
                throw e
            } catch(e:Exception) {
                message.value=if(editions.value.isNotEmpty()) "Hors ligne : lecture des éditions enregistrées"
                    else "Journal indisponible. Vérifie la connexion et la publication GitHub."
            } finally {working.value=false}
        }
    }
    fun toggleRead(id:String)=repo.toggleRead(id)
    fun setTheme(t:String) {theme.value=t;repo.saveTheme(t)}
    fun setFont(f:Float) {font.value=f;repo.saveFont(f)}
}
