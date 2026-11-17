package uk.family.actudujour

import org.junit.Assert.*
import org.junit.Test

class ModelsTest {
    private val source = SourceRef("Source officielle","https://www.gov.uk/news/article","en",
        "2026-11-17T10:00:00+00:00")
    private val paragraph = "Les informations ci-dessous appartiennent uniquement à un test technique fictif. " .repeat(8).trim()

    private fun article(paragraphs:List<String>): Article {
        val count=paragraphs.joinToString(" ").split(Regex("\\s+")).size
        return Article("12345678901234567890","Monde", "Un article technique fictif pour le contrôle",paragraphs,
            null,listOf(source),count,3)
    }
    private fun edition(a:Article): Edition = Edition(1,"2026-11-17-fr-FR","2026-11-17",
        "2026-11-17T12:00:00+00:00","fr-FR","Europe/London","partielle",3,listOf(a))

    @Test fun rejectsShortHeadlines() {
        assertThrows(IllegalArgumentException::class.java) {
            valid(edition(article(listOf("Une ligne seulement."))))
        }
    }
    @Test fun acceptsLongArticle() {
        val a=article(List(6){paragraph})
        assertTrue(a.wordCount >= 280)
        assertEquals("2026-11-17-fr-FR", valid(edition(a)).id)
    }
    @Test fun acceptsOnlyHttpsPublicSourceUrls() {
        assertTrue(safeUrl("https://www.gov.uk/news/test"))
        assertFalse(safeUrl("http://example.com/insecure"))
        assertFalse(safeUrl("https://localhost/secret"))
    }
    @Test fun hashesPublicEditionForIntegrity() {
        assertEquals("2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",sha("hello".encodeToByteArray()))
    }
}
