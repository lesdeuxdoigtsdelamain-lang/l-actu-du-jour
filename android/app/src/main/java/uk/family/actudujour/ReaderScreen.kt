package uk.family.actudujour

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowForward
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Fullscreen
import androidx.compose.material.icons.filled.FullscreenExit
import androidx.compose.material.icons.filled.MenuBook
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil3.compose.AsyncImage
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale

private val fr=Locale.FRENCH
private val dateFmt=DateTimeFormatter.ofPattern("EEEE d MMMM yyyy",fr)
private fun niceDate(s:String):String=runCatching{
    LocalDate.parse(s).format(dateFmt).replaceFirstChar {it.uppercase()}
}.getOrDefault(s)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReaderApp(model:NewsViewModel,onBrowser:(String)->Unit,onFullScreen:(Boolean)->Unit) {
    val all by model.editions.collectAsState()
    val read by model.readIds.collectAsState()
    var chosenDate by remember { mutableStateOf<String?>(null) }
    var chosenArticle by remember { mutableStateOf<String?>(null) }
    var options by remember { mutableStateOf(false) }
    var full by remember { mutableStateOf(false) }
    val chosen=all.firstOrNull {it.date==chosenDate} ?: all.firstOrNull()
    val article=chosen?.articles?.firstOrNull { it.id==chosenArticle }
    val back:()->Unit={ chosenArticle=null; full=false;onFullScreen(false) }

    if(options) {
        AlertDialog(onDismissRequest={options=false},title={Text("Préférences de lecture")},text={
            Column(verticalArrangement=Arrangement.spacedBy(16.dp)) {
                Text("Apparence")
                Row(horizontalArrangement=Arrangement.spacedBy(6.dp)) {
                    for(t in listOf("Clair","Sépia","Sombre")) {
                        FilterChip(selected=model.theme.value==t,onClick={model.setTheme(t)},
                            label={Text(t,fontSize=12.sp)})
                    }
                }
                Text("Taille de police : ${(model.font.value*100).toInt()} %")
                Slider(value=model.font.value,onValueChange={model.setFont(it)},
                    valueRange=0.8f..1.55f,steps=14)
                Text("Les articles sont conservés pendant 7 jours. Aucune publicité ni collecte de données.",
                    style=MaterialTheme.typography.bodySmall)
            }
        },confirmButton={TextButton(onClick={options=false}){Text("Fermer")}})
    }

    Scaffold(topBar={
        if(!full) {
            TopAppBar(title={Text(if(article==null)"L'actu du jour" else "Lecture",
                fontWeight=FontWeight.SemiBold)},
                navigationIcon={if(article!=null) { IconButton(onClick=back){
                    Icon(Icons.Default.ArrowBack,"Retour")
                } } },
                actions={
                    if(article==null) {
                        IconButton(onClick={model.refresh()},enabled=!model.working.value){
                            Icon(Icons.Default.Refresh,"Actualiser")
                        }
                        IconButton(onClick={options=true}){Icon(Icons.Default.Settings,"Réglages")}
                    } else {
                        IconButton(onClick={
                            full=!full;onFullScreen(full)
                        }){Icon(Icons.Default.Fullscreen,"Plein écran")}
                    }
                })
        }
    }) { padding ->
        if(article!=null && chosen!=null) {
            val index=chosen.articles.indexOfFirst {it.id==article.id}
            key(article.id) {
                ArticleReader(article=article,font=model.font.value,full=full,read=article.id in read,
                    modifier=Modifier.padding(padding),
                    onBack=back,onFull={full=!full;onFullScreen(full)},
                    onRead={model.toggleRead(article.id)},onNext={
                        if(index>=0 && index<chosen.articles.lastIndex) chosenArticle=chosen.articles[index+1].id
                        else back()
                    },hasNext=index<chosen.articles.lastIndex,onBrowser=onBrowser)
            }
        } else {
            Column(modifier=Modifier.fillMaxSize().padding(padding)) {
                if(model.working.value) LinearProgressIndicator(modifier=Modifier.fillMaxWidth())
                if(chosen==null) {
                    Column(Modifier.fillMaxSize().padding(28.dp),
                        horizontalAlignment=Alignment.CenterHorizontally,
                        verticalArrangement=Arrangement.Center) {
                        Icon(Icons.Default.MenuBook,null,modifier=Modifier.size(58.dp),
                            tint=MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.height(18.dp))
                        Text("Aucune édition téléchargée",style=MaterialTheme.typography.titleLarge)
                        Spacer(Modifier.height(10.dp))
                        Text("Le premier journal apparaîtra ici dès sa publication. La lecture hors ligne sera ensuite disponible.",
                            style=MaterialTheme.typography.bodyMedium)
                        Spacer(Modifier.height(20.dp))
                        Button(onClick={model.refresh()},enabled=!model.working.value){Text("Vérifier maintenant")}
                        if(model.message.value.isNotBlank()) {
                            Spacer(Modifier.height(12.dp));Text(model.message.value,
                                style=MaterialTheme.typography.bodySmall)
                        }
                    }
                } else {
                    LazyRow(contentPadding=PaddingValues(horizontal=16.dp),
                        horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                        items(all,key={it.id}) { e ->
                            FilterChip(selected=chosen.date==e.date,
                                onClick={chosenDate=e.date},label={Text(LocalDate.parse(e.date)
                                    .format(DateTimeFormatter.ofPattern("dd MMM",fr)))})
                        }
                    }
                    LazyColumn(contentPadding=PaddingValues(start=16.dp,end=16.dp,bottom=24.dp),
                        verticalArrangement=Arrangement.spacedBy(14.dp)) {
                        item {
                            Spacer(Modifier.height(4.dp))
                            Text(niceDate(chosen.date),style=MaterialTheme.typography.headlineSmall,
                                fontWeight=FontWeight.Medium)
                            Text("${chosen.articles.size} articles · ${chosen.minutes} min de lecture",
                                color=MaterialTheme.colorScheme.onSurfaceVariant)
                            if(chosen.status=="partielle") Text("Édition partielle : seules les actualités assez documentées ont été retenues.",
                                style=MaterialTheme.typography.bodySmall)
                            if(chosen.date!=ukToday().toString()) Text("Édition archivée — pas le journal d'aujourd'hui",
                                style=MaterialTheme.typography.bodySmall)
                        }
                        items(chosen.articles,key={it.id}) { a ->
                            NewsCard(a,a.id in read,onClick={chosenArticle=a.id})
                        }
                        if(model.message.value.isNotBlank()) item {
                            Text(model.message.value,style=MaterialTheme.typography.bodySmall,
                                color=MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun NewsCard(a:Article,read:Boolean,onClick:()->Unit) {
    Surface(onClick=onClick,shape=RoundedCornerShape(12.dp),tonalElevation=1.dp) {
        Row(modifier=Modifier.fillMaxWidth().padding(10.dp),
            verticalAlignment=Alignment.CenterVertically,
            horizontalArrangement=Arrangement.spacedBy(12.dp)) {
            val replacement=painterResource(fallback(a.category))
            if(a.image!=null) {
                AsyncImage(model=a.image.url,contentDescription=a.image.alt,
                    error=replacement,placeholder=replacement,
                    modifier=Modifier.size(width=110.dp,height=95.dp).clip(RoundedCornerShape(8.dp)),
                    contentScale=ContentScale.Crop)
            } else {
                Image(painter=replacement,contentDescription="Illustration — ${a.category}",
                    modifier=Modifier.size(width=110.dp,height=95.dp).clip(RoundedCornerShape(8.dp)),
                    contentScale=ContentScale.Crop)
            }
            Column(modifier=Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(6.dp)) {
                Text(a.category,style=MaterialTheme.typography.labelSmall,
                    color=MaterialTheme.colorScheme.primary)
                Text(a.title,style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.SemiBold,
                    maxLines=3,overflow=TextOverflow.Ellipsis)
                Row(verticalAlignment=Alignment.CenterVertically,
                    horizontalArrangement=Arrangement.spacedBy(6.dp)) {
                    Text("${a.minutes} min",style=MaterialTheme.typography.labelSmall)
                    if(read){Icon(Icons.Default.CheckCircle,null,modifier=Modifier.size(14.dp));
                        Text("Lu",style=MaterialTheme.typography.labelSmall)}
                }
                if(a.image!=null)Text("Photo : ${a.image.credit} (${a.image.license})",
                    maxLines=1,overflow=TextOverflow.Ellipsis,style=MaterialTheme.typography.labelSmall,
                    color=MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun ArticleReader(article:Article,font:Float,full:Boolean,read:Boolean,
    modifier:Modifier=Modifier,onBack:()->Unit,onFull:()->Unit,onRead:()->Unit,
    onNext:()->Unit,hasNext:Boolean,onBrowser:(String)->Unit) {
    val typography=if(font>=0.8f) (19*font).sp else 19.sp
    val lineHeight=(31*font).sp
    Column(modifier=Modifier.fillMaxSize().then(modifier)
        .verticalScroll(rememberScrollState()).padding(horizontal=22.dp,vertical=16.dp)) {
        if(full) {
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween) {
                TextButton(onClick=onBack){Icon(Icons.Default.ArrowBack,null);Text("Retour")}
                IconButton(onClick=onFull){Icon(Icons.Default.FullscreenExit,"Quitter le plein écran")}
            }
        }
        Text(article.category.uppercase(fr),style=MaterialTheme.typography.labelMedium,
            color=MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(10.dp))
        Text(article.title,fontSize=(27*font.coerceIn(0.8f,1.5f)).sp,
            fontWeight=FontWeight.Bold,lineHeight=(33*font).sp)
        Spacer(Modifier.height(12.dp))
        Text("${article.minutes} min · ${article.wordCount} mots",
            style=MaterialTheme.typography.labelMedium,
            color=MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(20.dp))
        for(p in article.paragraphs){
            Text(p,fontSize=typography,lineHeight=lineHeight,fontFamily=FontFamily.Serif)
            Spacer(Modifier.height(18.dp))
        }
        HorizontalDivider()
        Spacer(Modifier.height(14.dp))
        Text("Sources",style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Bold)
        Spacer(Modifier.height(12.dp))
        for(ref in article.sources) {
            Text("• ${ref.publisher} — ouvrir l'article original",
                modifier=Modifier.fillMaxWidth().clickable {onBrowser(ref.url)}
                    .padding(vertical=7.dp),color=MaterialTheme.colorScheme.primary,
                style=MaterialTheme.typography.bodyMedium)
        }
        Spacer(Modifier.height(20.dp))
        Row(horizontalArrangement=Arrangement.spacedBy(12.dp),
            verticalAlignment=Alignment.CenterVertically) {
            OutlinedButton(onClick=onRead){
                Icon(if(read)Icons.Default.Check else Icons.Default.CheckCircle,null)
                Spacer(Modifier.width(6.dp));Text(if(read)"Marqué comme lu" else "Marquer comme lu")
            }
        }
        Spacer(Modifier.height(12.dp))
        Button(onClick=onNext,modifier=Modifier.fillMaxWidth()) {
            Text(if(hasNext)"Article suivant" else "Retour au journal")
            Spacer(Modifier.width(8.dp));Icon(Icons.Default.ArrowForward,null)
        }
        Spacer(Modifier.height(30.dp))
    }
}
