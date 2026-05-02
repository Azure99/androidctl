import org.jlleitschuh.gradle.ktlint.KtlintExtension

// Top-level build file where you can add configuration options common to all sub-projects/modules.
val ktlintVersion = libs.versions.ktlint.get()

fun KtlintExtension.applySharedKtlintConfig() {
    version.set(ktlintVersion)
    android.set(true)
    outputToConsole.set(true)
    ignoreFailures.set(false)
    filter {
        exclude("**/build/**")
    }
}

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.ktlint)
    alias(libs.plugins.detekt) apply false
}

configure<KtlintExtension> {
    applySharedKtlintConfig()
}

subprojects {
    apply(plugin = "org.jlleitschuh.gradle.ktlint")

    extensions.configure<KtlintExtension> {
        applySharedKtlintConfig()
    }
}

tasks.register("qualityCheck") {
    group = "verification"
    description = "Runs the default local Android quality gate."
    dependsOn(
        ":ktlintCheck",
        ":app:ktlintCheck",
        ":app:detektMain",
        ":app:detektTest",
        ":app:lintDebug",
        ":app:testDebugUnitTest",
    )
}
