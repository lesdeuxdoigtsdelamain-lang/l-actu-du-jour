package uk.family.actudujour

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.compose.material3.*
import androidx.compose.ui.graphics.Color

class MainActivity : ComponentActivity() {
    private val model: NewsViewModel by viewModels()
    private val askPermission=registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        DailyScheduler.schedule(this)
        setContent {
            val theme=model.theme.value
            val scheme=when(theme) {
                "Sombre" -> darkColorScheme(primary=Color(0xFFB4D7C8),
                    background=Color(0xFF151515),surface=Color(0xFF151515))
                "Sépia" -> lightColorScheme(primary=Color(0xFF66503A),
                    background=Color(0xFFF8F0E2),surface=Color(0xFFF8F0E2),
                    surfaceVariant=Color(0xFFF0E3D1))
                else -> lightColorScheme(primary=Color(0xFF1B5661),
                    background=Color.White,surface=Color.White,
                    surfaceVariant=Color(0xFFEDF1F2))
            }
            MaterialTheme(colorScheme=scheme) {
                ReaderApp(model,onBrowser={url->
                    if(safeUrl(url))runCatching { startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(url))) }
                },onFullScreen={enabled->
                    val controller=WindowCompat.getInsetsController(window,window.decorView)
                    if(enabled)controller.hide(WindowInsetsCompat.Type.systemBars())
                    else controller.show(WindowInsetsCompat.Type.systemBars())
                })
            }
        }
        if(Build.VERSION.SDK_INT>=33 && ContextCompat.checkSelfPermission(
                this,Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED) {
            askPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    override fun onStart(){super.onStart();model.refresh()}
    override fun onStop(){
        WindowCompat.getInsetsController(window,window.decorView).show(WindowInsetsCompat.Type.systemBars())
        super.onStop()
    }
}
