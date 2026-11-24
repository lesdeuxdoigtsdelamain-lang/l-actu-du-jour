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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil3.compose.AsyncImage
import java.time.LocalDate
import java.time.OffsetDateTime
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
    val extras by model.extras.collectAsState()
    val hadith by model.hadith.collectAsState()
    val alerts = activeAlerts(extras, model.clock.value)
    val today = model.clock.value.atZone(london).toLocalDate()
    var chosenDate by remember { mutableStateOf<String?>(null) }
    var chosenArticle by remember { mutableStateOf<String?>(null) }
    var options by remember { mutableStateOf(false) }
    var full by remember { mutableStateOf(false) }
    val chosen=all.firstOrNull {it.date==chosenDate} ?: all.firstOrNull()
    val supplementDay=supplementDate(chosenDate,chosen?.date,today)
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
                LazyColumn(contentPadding=PaddingValues(start=16.dp,end=16.dp,bottom=24.dp),
                    verticalArrangement=Arrangement.spacedBy(14.dp)) {
                    // Live alerts always lead the feed, even while an archive is selected.
                    items(alerts,key={"alert-${it.id}"}) { alert ->
                        UrgentCard(alert,model.font.value,onBrowser)
                    }
                    item(key="today") {
                        Column(Modifier.fillMaxWidth().padding(top=4.dp),
                            verticalArrangement=Arrangement.spacedBy(3.dp)) {
                            Text("Aujourd'hui",style=MaterialTheme.typography.labelMedium,
                                color=MaterialTheme.colorScheme.primary)
                            Text(niceDate(today.toString()),style=MaterialTheme.typography.headlineSmall,
                                fontWeight=FontWeight.Medium)
                        }
                    }
                    if(chosen!=null) {
                        item {
                            LazyRow(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                item {
                                    FilterChip(selected=chosenDate==null,onClick={chosenDate=null},
                                        label={Text("Aujourd'hui")})
                                }
                                items(all.filter { it.date!=today.toString() },key={it.id}) { e ->
                                    FilterChip(selected=chosenDate==e.date,
                                        onClick={chosenDate=e.date},label={Text(LocalDate.parse(e.date)
                                            .format(DateTimeFormatter.ofPattern("dd MMM",fr)))})
                                }
                            }
                        }
                        item {
                            Spacer(Modifier.height(4.dp))
                            Text(if(chosen.date==today.toString())"Votre journal du jour"
                                else "Édition du ${niceDate(chosen.date)}",
                                style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.Medium)
                            Text("${chosen.articles.size} articles · ${chosen.minutes} min de lecture",
                                color=MaterialTheme.colorScheme.onSurfaceVariant)
                            if(chosen.status=="partielle") Text("Édition partielle : seules les actualités assez documentées ont été retenues.",
                                style=MaterialTheme.typography.bodySmall)
                            if(chosen.date!=today.toString()) Text(
                                if(chosenDate==null)"Dernière édition disponible. Le journal d'aujourd'hui est attendu."
                                else "Vous consultez une édition archivée.",
                                style=MaterialTheme.typography.bodySmall)
                        }
                        items(chosen.articles,key={it.id}) { a ->
                            NewsCard(a,a.id in read,onClick={chosenArticle=a.id})
                        }
                    } else {
                        item {
                            Column(Modifier.fillMaxWidth().padding(horizontal=12.dp,vertical=28.dp),
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
                            }
                        }
                    }
                    val note=historyFor(extras,supplementDay,today)
                    if(note!=null) item(key="history-${note.editionDate}") {
                        HistoryCard(note,model.font.value,onBrowser)
                    }
                    val dailyHadith=hadithFor(hadith,supplementDay,today)
                    if(dailyHadith!=null) item(key="hadith-${dailyHadith.editionDate}") {
                        HadithCard(dailyHadith,model.font.value,onBrowser)
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

@Composable
private fun UrgentCard(alert:BreakingAlert,font:Float,onBrowser:(String)->Unit) {
    // White on dark red remains readable in all three app themes.
    Surface(color=Color(0xFFA11520),contentColor=Color.White,shape=RoundedCornerShape(12.dp)) {
        Column(Modifier.fillMaxWidth().padding(16.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
            val time=OffsetDateTime.parse(alert.publishedAt).atZoneSameInstant(london)
                .format(DateTimeFormatter.ofPattern("dd MMM · HH:mm",fr))
            Text("URGENT · $time (heure UK)",fontSize=(12*font).sp,fontWeight=FontWeight.Bold)
            Text(alert.title,fontSize=(19*font).sp,lineHeight=(25*font).sp,fontWeight=FontWeight.Bold)
            if(alert.summary.isNotBlank()) Text(alert.summary,fontSize=(15*font).sp,lineHeight=(21*font).sp)
            for(source in alert.sources) {
                TextButton(onClick={onBrowser(source.url)},
                    colors=ButtonDefaults.textButtonColors(contentColor=Color.White),
                    contentPadding=PaddingValues(horizontal=0.dp,vertical=4.dp)) {
                    Text("Source : ${source.publisher}",fontSize=(13*font).sp)
                }
            }
        }
    }
}

@Composable
private fun HistoryCard(note:HistoryNote,font:Float,onBrowser:(String)->Unit) {
    Surface(shape=RoundedCornerShape(12.dp),tonalElevation=1.dp) {
        Column(Modifier.fillMaxWidth().padding(18.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
            Text("Ce jour-là",style=MaterialTheme.typography.titleMedium,
                fontWeight=FontWeight.Bold,color=MaterialTheme.colorScheme.primary)
            Text(LocalDate.parse(note.eventDate).format(DateTimeFormatter.ofPattern("d MMMM yyyy",fr)),
                fontSize=(20*font).sp,lineHeight=(26*font).sp,fontWeight=FontWeight.SemiBold)
            Text(note.lines.joinToString("\n"),fontSize=(16*font).sp,lineHeight=(24*font).sp)
            for(source in note.sources) {
                TextButton(onClick={onBrowser(source.url)},
                    contentPadding=PaddingValues(horizontal=0.dp,vertical=4.dp)) {
                    Text("Source : ${source.publisher}",fontSize=(13*font).sp)
                }
            }
        }
    }
}

@Composable
private fun HadithCard(note:HadithNote,font:Float,onBrowser:(String)->Unit) {
    Surface(shape=RoundedCornerShape(12.dp),tonalElevation=1.dp) {
        Column(Modifier.fillMaxWidth().padding(18.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
            Text("Hadith du jour",style=MaterialTheme.typography.titleMedium,
                fontWeight=FontWeight.Bold,color=MaterialTheme.colorScheme.primary)
            Text(niceDate(note.editionDate),style=MaterialTheme.typography.labelMedium,
                color=MaterialTheme.colorScheme.onSurfaceVariant)
            Text(note.arabic,modifier=Modifier.fillMaxWidth(),fontSize=(23*font).sp,
                lineHeight=(38*font).sp,textAlign=TextAlign.Right,
                style=MaterialTheme.typography.bodyLarge.copy(textDirection=TextDirection.Rtl))
            Text(note.narratorAr,modifier=Modifier.fillMaxWidth(),fontSize=(15*font).sp,
                lineHeight=(24*font).sp,textAlign=TextAlign.Right,
                style=MaterialTheme.typography.bodyMedium.copy(textDirection=TextDirection.Rtl))
            Text(note.referenceAr,modifier=Modifier.fillMaxWidth(),fontSize=(15*font).sp,
                lineHeight=(24*font).sp,textAlign=TextAlign.Right,
                style=MaterialTheme.typography.bodyMedium.copy(textDirection=TextDirection.Rtl))
            Text(note.authenticityFr,fontSize=(13*font).sp,lineHeight=(20*font).sp,
                color=MaterialTheme.colorScheme.onSurfaceVariant)
            HorizontalDivider()
            Text("Traduction du sens",fontSize=(14*font).sp,fontWeight=FontWeight.SemiBold)
            Text(note.translationFr,fontSize=(16*font).sp,lineHeight=(25*font).sp)
            Text("Explication brève",fontSize=(14*font).sp,fontWeight=FontWeight.SemiBold)
            Text(note.explanationFr,fontSize=(16*font).sp,lineHeight=(25*font).sp)
            for(source in note.sources) {
                TextButton(onClick={onBrowser(source.url)},
                    contentPadding=PaddingValues(horizontal=0.dp,vertical=4.dp)) {
                    Text("Source arabe : ${source.scholar}\n${source.publisher}",
                        fontSize=(13*font).sp,lineHeight=(20*font).sp)
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
