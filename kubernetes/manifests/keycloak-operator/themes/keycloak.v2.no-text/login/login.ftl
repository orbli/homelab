<#import "template.ftl" as layout>
<#import "field.ftl" as field>
<#import "buttons.ftl" as buttons>
<#import "social-providers.ftl" as identityProviders>
<#import "passkeys.ftl" as passkeys>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password') displayInfo=realm.password && realm.registrationAllowed && !registrationDisabled??; section>
<!-- template: login.ftl -->

    <#if section = "header">
        <#-- Header section intentionally empty: page title ("Sign in to your
             account") is suppressed. template.ftl still renders the wrapping
             <h1 id="kc-page-title"> element, but it ends up empty and CSS
             in resources/css/extra.css hides the surrounding header bar. -->
    <#elseif section = "form">
        <#-- Form section intentionally empty: username/password text-box
             login is removed for this realm. Users sign in exclusively via
             the IdP buttons rendered by the socialProviders section below
             (Google, in our case). The realm still has realm.password=true
             so that the socialProviders section's guard
             (realm.password && social.providers??) continues to render. -->
        <#-- (Original block removed: <div id="kc-form"> + <form id="kc-form-login">
             with username/password fields, Remember-me checkbox, and the
             Sign-in submit button. Plus the @passkeys.conditionalUIData call,
             which only makes sense when the password form is present.) -->
    <#elseif section = "socialProviders" >
        <#if realm.password && social.providers?? && social.providers?has_content>
            <@identityProviders.show social=social/>
        </#if>
    <#elseif section = "info" >
        <#if realm.password && realm.registrationAllowed && !registrationDisabled??>
            <div id="kc-registration-container">
                <div id="kc-registration">
                    <span>${msg("noAccount")} <a href="${url.registrationUrl}">${msg("doRegister")}</a></span>
                </div>
            </div>
        </#if>
    </#if>

</@layout.registrationLayout>
