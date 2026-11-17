package uk.family.actudujour

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch
import androidx.compose.runtime.mutableStateOf

class NewsViewModel(app: Application):AndroidViewModel(app) {
    val repo=NewsRepository(app)
    val editions=repo.editions
    val readIds=repo.readIds
    val working=mutableStateOf(false)
    val message=mutableStateOf("")
    val theme=mutableStateOf(repo.theme())
    val font=mutableStateOf(repo.fontSize())
    fun refresh() {
        if(working.value)return
        working.value=true
        viewModelScope.launch {
            try {
                val changed=repo.refresh()
                message.value=if(changed)"Nouvelle édition téléchargée" else "Journal à jour"
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
