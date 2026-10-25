import org.jlleitschuh.gradle.ktlint.KtlintExtension

val ktlintVersion = libs.versions.ktlint.get()

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.ktlint)
    alias(libs.plugins.detekt) apply false
}

allprojects {
    apply(plugin = "org.jlleitschuh.gradle.ktlint")

    extensions.configure<KtlintExtension> {
        version.set(ktlintVersion)
        android.set(true)
        filter {
            exclude("**/build/**")
        }
    }
}

tasks.register("lintCheck") {
    group = "verification"
    description = "Runs Kotlin formatting, static analysis and Android lint checks."
    dependsOn(
        ":ktlintCheck",
        ":app:ktlintCheck",
        ":app:detektMain",
        ":app:detektTest",
        ":app:lintDebug",
    )
}

tasks.register("qualityCheck") {
    group = "verification"
    description = "Runs the default local Android quality gate."
    dependsOn("lintCheck", ":app:testDebugUnitTest")
}
