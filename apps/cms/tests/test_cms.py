"""Pages and the journal (MST spec section 50).

The property worth guarding: a draft or a future-dated post must be a 404 to
the public even when somebody has the URL, while staff can still preview it.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cms.models import Page, Post, PostCategory
from apps.core.tests.factories import create_staff, create_user


class VisibilityTests(TestCase):
    def setUp(self):
        self.category = PostCategory.objects.create(name="Lookbooks")
        self.live = Post.objects.create(
            title="Festive edit", body="<p>Silk.</p>", is_published=True,
            category=self.category,
        )
        self.draft = Post.objects.create(
            title="Unfinished", body="<p>Notes.</p>", is_published=False
        )
        self.scheduled = Post.objects.create(
            title="Next week", body="<p>Soon.</p>", is_published=True,
            published_at=timezone.now() + timezone.timedelta(days=7),
        )

    def test_the_public_sees_only_live_posts(self):
        response = self.client.get(reverse("cms:post_list"))
        self.assertContains(response, "Festive edit")
        self.assertNotContains(response, "Unfinished")
        self.assertNotContains(response, "Next week")

    def test_a_draft_is_a_404_even_with_the_url(self):
        response = self.client.get(self.draft.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_a_scheduled_post_is_a_404_until_its_date(self):
        response = self.client.get(self.scheduled.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_staff_can_preview_a_draft(self):
        self.client.force_login(create_staff(email="editor@example.test"))
        self.assertEqual(self.client.get(self.draft.get_absolute_url()).status_code, 200)

    def test_a_shopper_cannot_preview_a_draft(self):
        self.client.force_login(create_user(email="shopper@example.test"))
        self.assertEqual(self.client.get(self.draft.get_absolute_url()).status_code, 404)

    def test_a_live_post_reads_fine(self):
        response = self.client.get(self.live.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Festive edit")


class PostTests(TestCase):
    def test_a_slug_is_derived_from_the_title(self):
        post = Post.objects.create(title="How to drape a saree", body="x")
        self.assertEqual(post.slug, "how-to-drape-a-saree")

    def test_a_byline_survives_the_account_being_removed(self):
        author = create_staff(email="writer@example.test")
        post = Post.objects.create(title="Silk guide", body="x", author=author)
        label = post.author_label
        self.assertTrue(label)
        author.delete()
        post.refresh_from_db()
        self.assertIsNone(post.author)
        self.assertEqual(post.author_label, label)

    def test_tags_are_split_for_display(self):
        post = Post.objects.create(title="Tagged", body="x", tags="silk, festive , ")
        self.assertEqual(post.tag_list, ["silk", "festive"])

    def test_reading_a_post_counts_a_view(self):
        post = Post.objects.create(title="Counted", body="x", is_published=True)
        self.client.get(post.get_absolute_url())
        post.refresh_from_db()
        self.assertEqual(post.view_count, 1)

    def test_a_scheduled_post_reports_itself_as_such(self):
        post = Post.objects.create(
            title="Later", body="x", is_published=True,
            published_at=timezone.now() + timezone.timedelta(days=1),
        )
        self.assertTrue(post.is_scheduled)

    def test_filtering_by_category(self):
        lookbooks = PostCategory.objects.create(name="Lookbooks")
        guides = PostCategory.objects.create(name="Guides")
        Post.objects.create(title="A lookbook", body="x", is_published=True, category=lookbooks)
        Post.objects.create(title="A guide", body="x", is_published=True, category=guides)

        response = self.client.get(lookbooks.get_absolute_url())
        self.assertContains(response, "A lookbook")
        self.assertNotContains(response, "A guide")


class PageTests(TestCase):
    def test_a_published_page_renders(self):
        page = Page.objects.create(title="Returns policy", body="<p>Thirty days.</p>")
        response = self.client.get(page.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thirty days")

    def test_an_unpublished_page_is_hidden(self):
        page = Page.objects.create(title="Coming soon", body="x", is_published=False)
        self.assertEqual(self.client.get(page.get_absolute_url()).status_code, 404)

    def test_a_slug_is_derived_from_the_title(self):
        page = Page.objects.create(title="Terms & Conditions", body="x")
        self.assertEqual(page.slug, "terms-conditions")

    def test_blog_urls_are_not_swallowed_by_the_page_route(self):
        """A page slugged 'blog' must not shadow the journal."""
        Page.objects.create(title="Blog", slug="blog", body="x")
        response = self.client.get(reverse("cms:post_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "cms/page")
