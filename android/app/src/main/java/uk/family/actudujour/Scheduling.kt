package uk.family.actudujour

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.*
import java.time.Duration
import java.time.Instant
import java.time.ZonedDateTime
import java.util.concurrent.TimeUnit

object DailyScheduler {
    fun schedule(context:Context,now:Instant=Instant.now()) {
        val here=now.atZone(london)
        var next=here.toLocalDate().atTime(17,5).atZone(london)
        if(!next.isAfter(here)) next=next.plusDays(1)
        val delay=Duration.between(here,next).toMillis().coerceAtLeast(1000L)
        val req=OneTimeWorkRequestBuilder<JournalWorker>()
            .setInitialDelay(delay,TimeUnit.MILLISECONDS)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork("journal-1705",
            ExistingWorkPolicy.REPLACE,req)
    }
}
class JournalWorker(context:Context,params:WorkerParameters):CoroutineWorker(context,params) {
    override suspend fun doWork():Result {
        return try {
            val store=NewsRepository(applicationContext)
            val updated=store.refresh()
            val today=store.editions.value.firstOrNull()
            if(updated && today?.date==ukToday().toString())notifyIfNew(applicationContext,today.id)
            DailyScheduler.schedule(applicationContext)
            Result.success()
        } catch (_:Exception) {
            // WorkManager retry may delay the alert. Re-check on next app launch too.
            if(runAttemptCount<2)Result.retry() else {
                DailyScheduler.schedule(applicationContext)
                Result.success()
            }
        }
    }
}
private fun notifyIfNew(context:Context,editionId:String) {
    if(Build.VERSION.SDK_INT>=33 && ContextCompat.checkSelfPermission(context,
            Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return
    val preferences=context.getSharedPreferences("reader",Context.MODE_PRIVATE)
    if(preferences.getString("last_notified","")==editionId)return
    val manager=context.getSystemService(NotificationManager::class.java)
    manager.createNotificationChannel(NotificationChannel("daily","Journal quotidien",
        NotificationManager.IMPORTANCE_DEFAULT))
    val pending=PendingIntent.getActivity(context,0,Intent(context,MainActivity::class.java),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    val notification=NotificationCompat.Builder(context,"daily")
        .setSmallIcon(R.drawable.ic_world)
        .setContentTitle("L'actu du jour est disponible")
        .setContentText("Votre journal du jour est prêt à lire, même hors ligne.")
        .setContentIntent(pending).setAutoCancel(true).build()
    try {
        manager.notify(2026,notification)
        preferences.edit().putString("last_notified",editionId).apply()
    } catch (_:SecurityException) { }
}
class StartupReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        if(intent.action==Intent.ACTION_BOOT_COMPLETED)DailyScheduler.schedule(context)
    }
}
